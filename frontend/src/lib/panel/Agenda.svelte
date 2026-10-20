<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import { panel } from '#lib/estado-panel.svelte';
	import type { CitaAgenda, EstadoCita, FichaPaciente } from '#lib/types';
	import type { Icono } from '#lib/icons';

	// Cada estado tiene su silueta: el médico distingue "completada" de "no vino" de reojo, sin
	// tener que leer la palabra en cada fila de la semana.
	const ICONO_ESTADO: Record<EstadoCita, Icono> = {
		confirmada: 'reloj',
		completada: 'check',
		no_vino: 'alerta',
		cancelada: 'cerrar'
	};

	const NOMBRES = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
	const MESES = [
		'enero',
		'febrero',
		'marzo',
		'abril',
		'mayo',
		'junio',
		'julio',
		'agosto',
		'septiembre',
		'octubre',
		'noviembre',
		'diciembre'
	];

	// El lunes de la semana que se está viendo. Se guarda como instante porque Svelte 5
	// compara por referencia: un Date nuevo en cada navegación fuerza el rederizado.
	let lunes = $state(new Date(inicio_semana(Date.now())));
	let citas = $state<CitaAgenda[]>([]);
	let cargando = $state(true);
	let mensaje = $state<string | null>(null);
	let ocupada = $state<string | null>(null);
	let mostrar_cerradas = $state(false);
	// La cita que el doctor acaba de cerrar se queda visible aunque el filtro esté en
	// "solo confirmadas": si no, pulsar "Completada" la borra de la pantalla y parece un fallo.
	let recientes = $state(new Set<string>());

	// La ficha del paciente, abierta desde una fila. Una sola a la vez: dos fichas abiertas en la
	// misma semana serían dos decisiones de borrado en pantalla y una de ellas se leería mal.
	let cita_abierta = $state<string | null>(null);
	let ficha = $state<FichaPaciente | null>(null);
	let cargando_ficha = $state(false);
	let pidiendo_confirmar = $state(false);
	let borrando = $state(false);
	// El aviso de lo que se acaba de borrar vive aparte de `mensaje`: la recarga que dispara
	// `panel.avisar()` limpia `mensaje`, y sin esta variable el médico se quedaría sin las cuentas.
	let aviso_supresion = $state<string | null>(null);

	// Feedback de dos botones que antes no se veía: "Hoy" (destello) y el cambio de estado de una
	// cita (la fila se ilumina un instante). Los dos se apagan solos; ninguno sostiene información.
	let flash_hoy = $state(false);
	let recién_cambiada = $state<string | null>(null);
	let temporizador_hoy: ReturnType<typeof setTimeout> | undefined;
	let temporizador_fila: ReturnType<typeof setTimeout> | undefined;

	$effect(() => () => {
		clearTimeout(temporizador_hoy);
		clearTimeout(temporizador_fila);
	});

	const cita_de_la_ficha = $derived(
		ficha && cita_abierta ? (ficha.citas.find((c) => c.id === cita_abierta) ?? null) : null
	);

	function inicio_semana(ms: number): number {
		const d = new Date(ms);
		d.setHours(0, 0, 0, 0);
		d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // getDay(): 0 = domingo
		return d.getTime();
	}

	function iso(d: Date): string {
		return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	}

	function sumar_dias(d: Date, dias: number): Date {
		const copia = new Date(d);
		copia.setDate(copia.getDate() + dias);
		return copia;
	}

	/** '2026-10-19' → Date a mediodía: así el cambio de huso nunca desplaza el día. */
	function fechaDe(iso_texto: string): Date {
		return new Date(`${iso_texto}T12:00:00`);
	}

	const hoy = $derived(iso(new Date()));
	// Clave de la ventana pedida: cambiar el lunes (flechas o "Hoy") vuelve a disparar la carga.
	const clave = $derived(iso(lunes));
	const dias = $derived(
		Array.from({ length: 7 }, (_, i) => {
			const fecha = iso(sumar_dias(lunes, i));
			return {
				fecha,
				nombre: NOMBRES[i],
				numero: fechaDe(fecha).getDate(),
				citas: citas.filter((c) => c.fecha === fecha)
			};
		})
	);
	const visibles = $derived(
		dias.map((dia) => ({
			...dia,
			citas: mostrar_cerradas
				? dia.citas
				: dia.citas.filter((c) => c.estado === 'confirmada' || recientes.has(c.id))
		}))
	);
	// Contamos desde `citas`, no desde la cabecera de la última carga: al cambiar un estado
	// la cabecera se queda vieja y el médico vería un número que ya no es cierto.
	const confirmadas = $derived(citas.filter((c) => c.estado === 'confirmada').length);
	const cerradas = $derived(citas.length - confirmadas);

	function etiqueta_semana(): string {
		const fin = sumar_dias(lunes, 6);
		const mismo_mes = lunes.getMonth() === fin.getMonth();
		const mismo_año = lunes.getFullYear() === fin.getFullYear();
		const izquierda = `${lunes.getDate()} ${MESES[lunes.getMonth()]}`;
		const derecha = `${fin.getDate()} ${MESES[fin.getMonth()]}${mismo_año ? '' : ` ${fin.getFullYear()}`}`;
		return mismo_mes ? `${izquierda}–${derecha}` : `${izquierda} · ${derecha}`;
	}

	async function cargar(desde: string) {
		cargando = true;
		mensaje = null;
		try {
			citas = (await api.agenda(desde, 7)).citas;
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo cargar la agenda';
			citas = [];
		} finally {
			cargando = false;
		}
	}

	$effect(() => {
		// Leer la clave es lo que ata el efecto al lunes mostrado: cada flecha recarga la ventana.
		// `panel.version` entra igual: si otra sección guarda una cita (reservar por el paciente
		// desde los avisos), esta semana se vuelve a leer en lugar de quedarse vieja.
		if (panel.version >= 0) cargar(clave);
	});

	async function cambiar(cita: CitaAgenda, estado: EstadoCita) {
		ocupada = cita.id;
		mensaje = null;
		try {
			const actualizada = await api.cambiarEstado(cita.id, estado);
			citas = citas.map((c) => (c.id === actualizada.id ? actualizada : c));
			recientes = new Set([...recientes, actualizada.id]);
			// La fila se marca con su propio id: si el médico cambia dos seguidas, la última manda.
			recién_cambiada = actualizada.id;
			clearTimeout(temporizador_fila);
			temporizador_fila = setTimeout(() => {
				if (recién_cambiada === actualizada.id) recién_cambiada = null;
			}, 460);
		} catch (error) {
			const texto = error instanceof ErrorApi ? error.message : 'No se pudo cambiar el estado';
			// En 409 recargamos primero: `cargar()` limpia el aviso, así que el mensaje va después.
			if (error instanceof ErrorApi && error.status === 409) await cargar(clave);
			mensaje = texto;
		} finally {
			ocupada = null;
		}
	}

	function paso(semanas: number) {
		lunes = new Date(inicio_semana(sumar_dias(lunes, semanas * 7).getTime()));
	}

	/**
	 * "Hoy" saltar a la semana actual Y recargarla. Antes solo se asignaba `lunes`, así que si ya
	 * estabas en esta semana la clave derivada no cambiaba, el `$effect` no corría y el botón
	 * parecía muerto. El destello informa del toque aunque la semana sea la misma.
	 */
	async function ir_a_hoy() {
		const nuevo = new Date(inicio_semana(Date.now()));
		const misma_semana = iso(nuevo) === clave;
		lunes = nuevo;
		flash_hoy = true;
		clearTimeout(temporizador_hoy);
		temporizador_hoy = setTimeout(() => (flash_hoy = false), 640);
		if (misma_semana) await cargar(clave);
	}

	function cerrar_ficha() {
		cita_abierta = null;
		ficha = null;
		pidiendo_confirmar = false;
	}

	/** Abrir (o volver a cerrar) la ficha de quien está en esa fila. */
	async function ver_ficha(cita: CitaAgenda) {
		if (cita_abierta === cita.id) {
			cerrar_ficha();
			return;
		}
		cargando_ficha = true;
		pidiendo_confirmar = false;
		mensaje = null;
		aviso_supresion = null;
		try {
			ficha = await api.fichaDeCita(cita.id);
			cita_abierta = cita.id;
		} catch (error) {
			// 404 aquí es normal: una cita sin ficha ya no tiene a quién señalar.
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo abrir la ficha';
			cerrar_ficha();
		} finally {
			cargando_ficha = false;
		}
	}

	/**
	 * Suprimir de verdad. Es el único botón del panel que borra datos de otra persona, así que pide
	 * un segundo clic con las cuentas de ESTA ficha delante, y al terminar avisa a las otras secciones:
	 * la lista de espera cambia de nombre y la cita se queda sin paciente en la misma pantalla.
	 */
	async function suprimir() {
		if (!ficha) return;
		borrando = true;
		try {
			const resultado = await api.suprimirPaciente(ficha.id);
			// Primero el aviso propio: `panel.avisar()` dispara la recarga de la semana, y esa recarga
			// borra `mensaje`. Las cuentas de lo suprimido tienen que seguir viéndose.
			aviso_supresion = resultado.aviso;
			cerrar_ficha();
			panel.avisar();
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo borrar la ficha';
			pidiendo_confirmar = false;
		} finally {
			borrando = false;
		}
	}

	/** Un instante guardado, con el minuto: la prueba del consentimiento se cita por la hora exacta. */
	function momento(instante: string): string {
		return new Date(instante).toLocaleString('es-ES', {
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	/** '2026-10-19' leído como día, nunca como instante: si no, el huso lo mueve. */
	function dia_legible(iso_fecha: string): string {
		return new Date(`${iso_fecha}T12:00:00`).toLocaleDateString('es-ES', {
			day: 'numeric',
			month: 'short',
			year: 'numeric'
		});
	}

	function ya_paso(cita: CitaAgenda): boolean {
		return new Date(cita.inicio_utc) < new Date();
	}
</script>

<section class="tarjeta">
	<div class="fila fila--entre">
		<h2><Icon name="calendario" tamaño={19} clase="ico ico--accion" /> Agenda</h2>
		<div class="fila">
			<button class="boton--suave boton--chico" onclick={() => paso(-1)} aria-label="Semana anterior">
				<Icon name="izquierda" tamaño={16} />
			</button>
			<span class="etiqueta semana">{etiqueta_semana()}</span>
			<button class="boton--suave boton--chico" onclick={() => paso(1)} aria-label="Semana siguiente">
				<Icon name="derecha" tamaño={16} />
			</button>
			<button
				class="boton--suave boton--chico"
				class:pulso--primario={flash_hoy}
				onclick={ir_a_hoy}
			>
				Hoy
			</button>
		</div>
	</div>

	{#if aviso_supresion && !cita_abierta}
		<p class="supresion" role="status">{aviso_supresion}</p>
	{/if}

	<p class="aviso">
		{#if cargando}
			Cargando…
		{:else}
			{confirmadas} cita{confirmadas === 1 ? '' : 's'} confirmada{confirmadas === 1 ? '' : 's'} esta semana.
			{#if cerradas > 0}
				<button class="boton--suave boton--chico" onclick={() => (mostrar_cerradas = !mostrar_cerradas)}>
					{mostrar_cerradas ? 'Ocultar' : 'Ver'} las {cerradas} cerradas
				</button>
			{/if}
		{/if}
	</p>

	{#each visibles as dia (dia.fecha)}
		<div class="dia" class:hoy={dia.fecha === hoy}>
			<div class="cabecera-dia">
				<strong>{dia.nombre}</strong>
				<span class="aviso">{dia.numero} {MESES[fechaDe(dia.fecha).getMonth()]}</span>
				{#if dia.fecha === hoy}<span class="marca">hoy</span>{/if}
			</div>

			{#if dia.citas.length === 0}
				<p class="vacio">Sin citas</p>
			{:else}
				{#each dia.citas as cita (cita.id)}
					<article
						class="cita"
						class:cerrada={cita.estado !== 'confirmada'}
						class:past={ya_paso(cita)}
						class:cambia={recién_cambiada === cita.id}
					>
						<span class="punto" style="background: {cita.color}" aria-hidden="true"></span>
						<span class="hora">{cita.hora.slice(0, 5)}–{cita.fin.slice(0, 5)}</span>
						<span class="qué">{cita.servicio ?? 'Servicio quitado'}</span>

						{#if cita.paciente}
							<span class="paciente">
								<button
									class="nombre"
									aria-expanded={cita_abierta === cita.id}
									aria-controls="ficha"
									onclick={() => ver_ficha(cita)}>{cita.paciente.nombre}</button>
								{#if cita.paciente.telefono}
									<a class="enlace" href="tel:{cita.paciente.telefono}"><Icon name="telefono" tamaño={14} clase="ico" />{cita.paciente.telefono}</a>
								{:else}
									<span class="aviso">sin teléfono</span>
								{/if}
							</span>
						{:else}
							<span class="aviso">paciente borrado</span>
						{/if}

						<span class="estado estado--{cita.estado}">
								<Icon name={ICONO_ESTADO[cita.estado]} tamaño={13} />{cita.estado}
							</span>

						{#if cita.estado === 'confirmada'}
							<button class="boton--suave boton--chico" disabled={ocupada === cita.id} onclick={() => cambiar(cita, 'completada')}>
								Completada
							</button>
							<button class="boton--suave boton--chico" disabled={ocupada === cita.id} onclick={() => cambiar(cita, 'no_vino')}>
								No vino
							</button>
							<button class="boton--peligro boton--chico" disabled={ocupada === cita.id} onclick={() => cambiar(cita, 'cancelada')}>
								Cancelar
							</button>
						{:else}
							<button class="boton--suave boton--chico" disabled={ocupada === cita.id} onclick={() => cambiar(cita, 'confirmada')}>
								Reconfirmar
							</button>
						{/if}
					</article>

					{#if cita_abierta === cita.id}
						<div class="ficha nace" id="ficha">
							{#if cargando_ficha}
								<p class="aviso">Abriendo su ficha…</p>
							{:else if ficha}
								{@const total_citas = ficha.citas.length}
								{@const total_pedidas = ficha.peticiones.length}

								<div class="fila fila--entre">
									<strong>{ficha.nombre}</strong>
									<button class="boton--suave boton--chico" onclick={cerrar_ficha}><Icon name="cerrar" tamaño={15} />Cerrar</button>
								</div>

								<p class="datos">
									{ficha.telefono ?? 'sin teléfono'} · {ficha.email ?? 'sin email'} · alta el {momento(ficha.creado_en)}
								</p>

								<p class="aviso">
									{total_citas} cita{total_citas === 1 ? '' : 's'} y {total_pedidas} {total_pedidas === 1 ? 'petición' : 'peticiones'} de la lista de espera guardadas con su nombre.
									{#if ficha.ultima_visita}
										Ultima visita: {dia_legible(ficha.ultima_visita)}.
									{:else}
										No consta última visita.
									{/if}
								</p>

								{#if cita_de_la_ficha}
									<p class="aviso">
										Esta cita de las {cita_de_la_ficha.hora.slice(0, 5)}
										{cita_de_la_ficha.origen === 'publico' ? 'la pidió desde su enlace público' : 'la apuntaste tú desde el panel'}
										y su consentimiento quedó guardado el {momento(cita_de_la_ficha.consentimiento_en)}.
										{#if cita_de_la_ficha.recordatorio_24h_en}
											El aviso de 24 h se envió el {momento(cita_de_la_ficha.recordatorio_24h_en)}.
										{:else}
											El aviso de 24 h no se ha enviado.
										{/if}
										{#if cita_de_la_ficha.recordatorio_1h_en}
											El de 1 h, el {momento(cita_de_la_ficha.recordatorio_1h_en)}.
										{/if}
										{#if cita_de_la_ficha.recordatorio_intentos > 0}
											Hay {cita_de_la_ficha.recordatorio_intentos} intento{cita_de_la_ficha.recordatorio_intentos === 1 ? '' : 's'} de aviso sin responder.
										{/if}
									</p>
								{/if}

								<div class="fila">
									<a
										class="boton--suave boton--chico enlace-boton"
										href={api.url_de_datos(ficha.id)}
										download><Icon name="descargar" tamaño={15} />Bajar sus datos</a>
									{#if !pidiendo_confirmar}
										<button class="boton--peligro boton--chico" onclick={() => (pidiendo_confirmar = true)}><Icon name="papelera" tamaño={15} />Borrar su ficha</button>
									{/if}
								</div>

								{#if pidiendo_confirmar}
									<div class="confirmar">
										<p>{ficha.aviso}</p>
										<div class="fila">
											<button class="boton--peligro boton--chico" disabled={borrando} onclick={suprimir}>{borrando ? 'Borrando…' : 'Sí, borrar su ficha'}</button>
											<button class="boton--suave boton--chico" disabled={borrando} onclick={() => (pidiendo_confirmar = false)}>Cancelar</button>
										</div>
									</div>
								{/if}

								{#if mensaje}
									<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje}</p>
								{/if}

							{/if}
						</div>
					{/if}

				{/each}
			{/if}
		</div>
	{/each}

	{#if mensaje && !cita_abierta}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje}</p>
	{/if}
</section>

<style>
	.dia {
		border-top: 1px solid var(--borde);
		padding: 0.6rem 0;
	}

	.dia.hoy {
		background: color-mix(in srgb, var(--primario) 5%, transparent);
	}

	.cabecera-dia {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
	}

	.marca {
		font-size: 0.75rem;
		padding: 0.05rem 0.4rem;
		border-radius: 999px;
		background: var(--primario);
		color: #fff;
	}

	.vacio {
		margin: 0.25rem 0 0;
		font-size: 0.9rem;
		opacity: 0.6;
	}

	.cita {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem;
		padding: 0.4rem 0;
	}

	.cita.cerrada,
	.cita.past {
		opacity: 0.55;
	}

	.hora {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
	}

	.paciente {
		display: flex;
		gap: 0.4rem;
		flex: 1 1 12rem;
	}

	.estado {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		font-size: 0.78rem;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
		border: 1px solid var(--borde);
	}

	.estado--confirmada {
		border-color: var(--primario);
		color: var(--primario);
	}

	.enlace {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		color: var(--primario);
		cursor: pointer;
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	/* La semana va en la misma línea que las flechas: sin el margen de arriba sería un salto. */
	.semana {
		margin-bottom: 0;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}

	/* El nombre es el único acceso a la ficha: parece texto de la fila, no un botón suelto. */
	.nombre {
		background: none;
		border: none;
		padding: 0;
		min-height: auto;
		font: inherit;
		font-weight: 600;
		color: var(--primario);
		text-decoration: underline;
		text-underline-offset: 2px;
		cursor: pointer;
	}

	/* Lo que se acaba de suprimir, con sus cuentas: se queda arriba hasta que se abre otra ficha. */
	.supresion {
		margin: 0.4rem 0 0;
		padding: 0.5rem 0.7rem;
		font-size: 0.9rem;
		line-height: 1.45;
		background: color-mix(in srgb, var(--peligro) 6%, var(--superficie));
		border: 1px solid color-mix(in srgb, var(--peligro) 30%, var(--borde));
		border-left: 3px solid var(--peligro);
		border-radius: 8px;
	}

	/* La ficha va pegada a su fila y con fondo propio: es información de una persona concreta, y si
	 * se leyera como un párrafo suelto de la semana se perdería de qué cita salió.
	 */
	.ficha {
		display: grid;
		gap: 0.4rem;
		margin: 0.2rem 0 0.6rem;
		padding: 0.7rem 0.8rem;
		background: var(--superficie);
		border: 1px solid var(--borde);
		border-radius: var(--radio);
		box-shadow: var(--sombra);
	}

	.ficha p {
		margin: 0;
		line-height: 1.45;
	}

	.ficha .datos {
		font-size: 0.92rem;
	}

	/* La confirmación del borrado: el texto del servidor con las cuentas reales, marcado en rojo. */
	.confirmar {
		display: grid;
		gap: 0.45rem;
		padding-left: 0.6rem;
		border-left: 3px solid var(--peligro);
	}

	.confirmar p {
		font-size: 0.9rem;
	}

	.enlace-boton {
		display: inline-flex;
		align-items: center;
		text-decoration: none;
		border: 1px solid var(--borde);
		border-radius: 10px;
	}
</style>
