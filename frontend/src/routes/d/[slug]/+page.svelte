<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import { PREFERENCIAS } from '#lib/types';
	import type {
		Cita,
		DiaDisponible,
		Hueco,
		ListaDeEsperaPublica,
		PreferenciaLista,
		Servicio
	} from '#lib/types';

	let { data } = $props();

	type Paso = 'servicio' | 'hora' | 'datos' | 'hecho';
	let paso = $state<Paso>('servicio');
	let servicio = $state<Servicio | null>(null);
	let dias = $state<DiaDisponible[]>([]);
	let fecha = $state<string | null>(null);
	let hueco = $state<Hueco | null>(null);
	let cargandoHoras = $state(false);
	let guardando = $state(false);
	let error = $state<string | null>(null);
	let cita = $state<Cita | null>(null);
	// Los días sin huecos no vienen en la respuesta; sin horizonte no se puede decir
	// «no queda nada entre el 18 y el 31» ni ofrecer un calendario para la lista.
	let horizonte = $state<{ desde: string; hasta: string } | null>(null);

	// Lista de espera: la demanda de un hueco que ahora no existe.
	let listaAbierta = $state(false);
	let listaGuardando = $state(false);
	let listaError = $state<string | null>(null);
	let listaHecha = $state<ListaDeEsperaPublica | null>(null);
	let preferencia = $state<PreferenciaLista>('cualquier_hora');
	// '' = cualquier día: hay pacientes que solo saben qué franja les viene bien.
	let diaPedido = $state('');

	let nombre = $state('');
	let telefono = $state('');
	let correo = $state('');
	let consentimiento = $state(false);
	// Consentimiento aparte: aceptar la reserva no es aceptar quedarse en la lista.
	let consentimientoLista = $state(false);

	const diasSemana = ['L', 'M', 'X', 'J', 'V', 'S', 'D'];

	function fechaDe(iso: string): Date {
		// Mediodía local: evitamos que un desfase de zona cambie el día al leer getDate().
		return new Date(`${iso}T12:00:00`);
	}

	function isoTexto(d: Date): string {
		return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
	}

	function letraDia(iso: string): string {
		const dia = fechaDe(iso).getDay(); // 0 = domingo
		return diasSemana[dia === 0 ? 6 : dia - 1];
	}

	const puedeEnviar = $derived(
		nombre.trim().length >= 2 && consentimiento && !!hueco && !!fecha && !guardando
	);

	/** Todos los días del horizonte, incluidos los vacíos: la lista se apunta precisamente a esos. */
	const diasHorizonte = $derived.by(() => {
		if (!horizonte) return [];
		const salida: string[] = [];
		const cursor = fechaDe(horizonte.desde);
		const fin = fechaDe(horizonte.hasta);
		while (cursor <= fin && salida.length < 70) {
			salida.push(isoTexto(cursor));
			cursor.setDate(cursor.getDate() + 1);
		}
		return salida;
	});

	const hayContacto = $derived(telefono.trim().length >= 6 || correo.trim().includes('@'));
	const puedeLista = $derived(
		nombre.trim().length >= 2 && consentimientoLista && hayContacto && !listaGuardando
	);
	const textoPreferencia = $derived(
		PREFERENCIAS.find((p) => p.valor === preferencia)?.texto ?? 'cualquier hora'
	);

	// RGPD: cuando el aviso sale de un servicio de envíos y no del buzón del médico, el tercero se nombra
	// en la misma pantalla donde se promete el aviso. `smtp` es el correo de la consulta, así que no hay
	// nada que declarar; un canal que aquí no esté escrito se declara como 'un servicio de envíos': menos
	// preciso y sigue siendo verdad, que es el orden correcto delante de un paciente.
	const SERVICIOS_DE_ENVIO: Record<string, string> = { brevo: 'Brevo' };
	const terceros = $derived(
		data.canal_de_avisos === 'smtp' || data.canal_de_avisos === 'simulado'
			? null
			: (SERVICIOS_DE_ENVIO[data.canal_de_avisos] ?? 'un servicio de envíos')
	);

	/** Tres verdades distintas: alta nueva, repetida y pendiente, repetida y ya avisada. */
	const tituloLista = $derived.by(() => {
		if (!listaHecha) return '';
		if (listaHecha.creado) return 'Te hemos apuntado a la lista de espera';
		return listaHecha.estado === 'contactada'
			? 'Ya habías pedido ese día y tu médico ya te avisó'
			: 'Ya estabas en la lista de espera';
	});

	/**
	 * Paso marcado en la guía de arriba. Es una secuencia real —servicio, día y hora, datos— así que
	 * numerarla aporta información: el paciente sabe cuánto le queda sin tener que leer la pantalla.
	 */
	const paso_actual = $derived(
		paso === 'hecho' ? 4 : paso === 'datos' ? 3 : paso === 'hora' ? 2 : 1
	);

	function clase_paso(n: number): string {
		if (n === paso_actual) return 'activo';
		return n < paso_actual ? 'hecho' : '';
	}

	function textoError(e: unknown): string {
		return e instanceof ErrorApi ? e.message : 'No pudimos conectar con la agenda. Inténtalo de nuevo.';
	}

	function nombreDia(iso: string): string {
		return iso ? fechaDe(iso).toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long' }) : '';
	}

	function diaCorto(iso: string): string {
		return iso
			? fechaDe(iso).toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric', month: 'short' })
			: '';
	}

	function horaLarga(valor: string): string {
		return valor.slice(0, 5);
	}

	async function cargarHoras(servicioElegido: Servicio) {
		servicio = servicioElegido;
		paso = 'hora';
		hueco = null;
		listaAbierta = false;
		listaHecha = null;
		listaError = null;
		cargandoHoras = true;
		error = null;
		try {
			const respuesta = await api.disponibilidad(data.slug, servicioElegido.id, 14);
			dias = respuesta.dias;
			horizonte = { desde: respuesta.desde, hasta: respuesta.hasta };
			fecha = dias[0]?.fecha ?? null;
			// Sin un solo hueco la lista es el único camino útil: la abrimos directamente.
			if (!dias.length) listaAbierta = true;
		} catch (e) {
			error = textoError(e);
		} finally {
			cargandoHoras = false;
		}
	}

	const huecosDelDia = $derived(dias.find((d) => d.fecha === fecha)?.huecos ?? []);

	function elegirDia(iso: string) {
		fecha = iso;
		hueco = null;
	}

	function elegirHueco(h: Hueco) {
		hueco = h;
		paso = 'datos';
		error = null;
	}

	async function recargarHoras() {
		if (!servicio) return;
		try {
			const respuesta = await api.disponibilidad(data.slug, servicio.id, 14);
			dias = respuesta.dias;
			horizonte = { desde: respuesta.desde, hasta: respuesta.hasta };
			if (fecha && !dias.some((d) => d.fecha === fecha)) fecha = dias[0]?.fecha ?? null;
		} catch {
			/* si la recarga falla dejamos las horas que ya veía el paciente */
		}
	}

	async function apuntarse() {
		if (!servicio || !puedeLista) return;
		listaGuardando = true;
		listaError = null;
		try {
			listaHecha = await api.apuntarse(data.slug, {
				servicio: servicio.id,
				fecha: diaPedido || null,
				preferencia,
				nombre: nombre.trim(),
				telefono: telefono.trim() || null,
				email: correo.trim() || null,
				consentimiento: true
			});
		} catch (e) {
			listaError = textoError(e);
		} finally {
			listaGuardando = false;
		}
	}

	async function confirmar() {
		if (!servicio || !fecha || !hueco || !puedeEnviar) return;
		guardando = true;
		error = null;
		try {
			cita = await api.reservar(data.slug, {
				servicio: servicio.id,
				fecha,
				// Viaja la hora local del doctor tal como la vio el paciente: la conversión a UTC
				// es del backend, para que el huso del navegador del paciente no importe.
				hora: hueco.inicio,
				nombre: nombre.trim(),
				telefono: telefono.trim() || null,
				email: correo.trim() || null,
				consentimiento: true
			});
			paso = 'hecho';
		} catch (e) {
			error = textoError(e);
			if (e instanceof ErrorApi && e.status === 409) {
				await recargarHoras();
				hueco = null;
			}
		} finally {
			guardando = false;
		}
	}

	function empezarDeNuevo() {
		paso = 'servicio';
		servicio = null;
		hueco = null;
		cita = null;
		nombre = '';
		telefono = '';
		correo = '';
		consentimiento = false;
		consentimientoLista = false;
		listaAbierta = false;
		listaHecha = null;
		listaError = null;
		diaPedido = '';
		preferencia = 'cualquier_hora';
		error = null;
	}
</script>

<svelte:head>
	<title>{data.doctor ? `Reservar cita · ${data.doctor.nombre}` : 'Enlace no válido'}</title>
</svelte:head>

<main class="contenedor">
	{#if data.error === 404 || !data.doctor}
		<h1 class="roto"><Icon name="alerta" tamaño={20} />Enlace no válido</h1>
		<p class="aviso">
			Este enlace no corresponde a ninguna agenda. Pídele a tu médico que te vuelva a pasar su
			enlace.
		</p>
	{:else}
		<header class="placa">
			<!-- La marca se dibuja sola al entrar (`.marca-diente`, en styles.css): es el saludo de la
			 consulta y el único ornamento de la página. -->
			<span class="marca-diente"><Icon name="diente" tamaño={44} /></span>
			<h1>{data.doctor.nombre}</h1>
			<p class="aviso">Reserva tu cita. Sin registro y sin llamadas.</p>
		</header>

		<ol class="pasos">
			<li class={clase_paso(1)}><Icon name="diente" tamaño={15} clase="ico ico--accion" />Servicio</li>
			<li class={clase_paso(2)}><Icon name="calendario" tamaño={15} />Día y hora</li>
			<li class={clase_paso(3)}><Icon name="telefono" tamaño={15} clase="ico ico--accion" />Tus datos</li>
		</ol>

		{#if error && paso !== 'datos'}
			<p class="error" role="alert"><Icon name="alerta" tamaño={15} clase="ico ico--accion ico--nota" />{error}</p>
		{/if}

		{#if paso === 'servicio'}
			<h2><Icon name="diente" tamaño={18} />¿Qué necesitas?</h2>
			<div class="lista">
				{#each data.servicios as s (s.id)}
					<button class="tarjeta opcion" type="button" onclick={() => cargarHoras(s)}>
						<!-- El color del servicio manda en el diente: es el mismo que verá luego en la agenda. -->
						<Icon name="diente" tamaño={22} estilo={`color: ${s.color}`} />
						<span class="nombre">{s.nombre}</span>
						<span class="aviso dura"><Icon name="reloj" tamaño={14} clase="ico ico--accion" />{s.duracion_minutos} min</span>
					</button>
				{:else}
					<p class="aviso">Este médico aún no tiene servicios activos.</p>
				{/each}
			</div>
		{:else if paso === 'hora'}
			<button class="boton boton--suave boton--chico" type="button" onclick={() => (paso = 'servicio')}>
				<Icon name="izquierda" tamaño={15} />Otro servicio
			</button>
			<h2><Icon name="diente" tamaño={18} />{servicio?.nombre} · {servicio?.duracion_minutos} min</h2>

			{#if cargandoHoras}
				<p class="aviso fila"><Icon name="recargar" tamaño={15} clase="giro" />Mirando la agenda…</p>
			{:else if dias.length}
				<div class="dias" role="tablist" aria-label="Días con huecos">
					{#each dias as d (d.fecha)}
						<button
							class="dia"
							class:activo={d.fecha === fecha}
							type="button"
							role="tab"
							aria-selected={d.fecha === fecha}
							onclick={() => elegirDia(d.fecha)}
						>
							<span class="dow">{letraDia(d.fecha)}</span>
							<span class="num">{fechaDe(d.fecha).getDate()}</span>
						</button>
					{/each}
				</div>
				<p class="aviso dia-elegido"><Icon name="calendario" tamaño={14} clase="ico ico--accion" />{nombreDia(fecha ?? '')}</p>

				<div class="rejilla">
					{#each huecosDelDia as h (h.inicio)}
						<button class="hora" type="button" onclick={() => elegirHueco(h)}>
							{horaLarga(h.inicio)}
						</button>
					{/each}
				</div>
			{:else if horizonte}
				<p class="aviso fila">
					<Icon name="reloj" tamaño={15} clase="ico ico--accion" />No queda ningún hueco de {servicio?.nombre.toLowerCase()} entre
					el {diaCorto(horizonte.desde)} y el {diaCorto(horizonte.hasta)}.
				</p>
			{:else}
				<p class="aviso fila"><Icon name="reloj" tamaño={15} clase="ico ico--accion" />Este médico no tiene horas libres ahora mismo.</p>
			{/if}

			{#if !cargandoHoras}
				{#if listaHecha}
					<div class="tarjeta confirmada nace">
						<p class="hecha">
							<Icon name="check" tamaño={17} /><strong>{tituloLista}</strong>
						</p>
						<p class="aviso">
							{#if listaHecha.estado === 'contactada'}
								Tu médico marcó esta petición como avisada. Si no le has encontrado, escríbele o
								llámale: sigue en su lista.
							{:else}
								Te avisará con {listaHecha.servicio} cuando quede un hueco
								{diaPedido ? `el ${nombreDia(diaPedido).toLowerCase()}` : 'algún día'} ·
								{textoPreferencia.toLowerCase()}.
								{#if !listaHecha.creado}No hace falta que te apuntes otra vez.{/if}
							{/if}
						</p>
						<button class="boton boton--suave" type="button" onclick={empezarDeNuevo}>
							<Icon name="diente" tamaño={16} />Buscar otra cosa
						</button>
					</div>
				{:else if listaAbierta}
					<h3 class="titulo-lista"><Icon name="campana" tamaño={17} clase="ico ico--accion" />Avísame cuando quede un hueco</h3>
					<p class="aviso">
						Tu médico ve esta lista en su panel y te llama cuando libera una hora. No es una
						reserva: todavía no tienes cita.
					</p>

					<form class="tarjeta" onsubmit={(e) => { e.preventDefault(); apuntarse(); }}>
						<div class="campo">
							<label class="etiqueta" for="lista-dia">Día que prefieres</label>
							<select id="lista-dia" bind:value={diaPedido}>
								<option value="">Cualquier día</option>
								{#each diasHorizonte as d (d)}
									<option value={d}>{nombreDia(d)}</option>
								{/each}
							</select>
						</div>
						<div class="campo">
							<label class="etiqueta" for="lista-franja">Franja</label>
							<select id="lista-franja" bind:value={preferencia}>
								{#each PREFERENCIAS as p (p.valor)}
									<option value={p.valor}>{p.texto}</option>
								{/each}
							</select>
						</div>
						<div class="campo">
							<label class="etiqueta" for="lista-nombre">Nombre y apellidos</label>
							<input id="lista-nombre" bind:value={nombre} autocomplete="name" required />
						</div>
						<div class="campo">
							<label class="etiqueta" for="lista-telefono">Teléfono</label>
							<input id="lista-telefono" type="tel" bind:value={telefono} autocomplete="tel" inputmode="tel" />
						</div>
						<div class="campo">
							<label class="etiqueta" for="lista-correo">Email (opcional)</label>
							<input id="lista-correo" type="email" bind:value={correo} autocomplete="email" />
							{#if !hayContacto}
								<span class="aviso">Necesitamos un teléfono o un email para poder avisarte.</span>
							{/if}
						</div>

						<label class="campo consentimiento" for="lista-consentimiento">
							<input id="lista-consentimiento" type="checkbox" bind:checked={consentimientoLista} />
							<span>
								Acepto que {data.doctor.nombre} guarde mi nombre y mi contacto mientras esté en la
								lista de espera. No guardamos nada de tu salud. Puedes pedir que se borre escribiéndole.
							</span>
						</label>

						<button class="boton" type="submit" disabled={!puedeLista}>
							{#if listaGuardando}
								<Icon name="recargar" tamaño={16} clase="giro" />Apuntando…
							{:else}
								<Icon name="personas" tamaño={16} />Apuntarme
							{/if}
						</button>

						{#if listaError}
							<p class="error" role="alert"><Icon name="alerta" tamaño={15} clase="ico ico--accion ico--nota" />{listaError}</p>
						{/if}
					</form>
				{:else}
					<button class="boton boton--suave enlace-lista" type="button" onclick={() => (listaAbierta = true)}>
						<Icon name="campana" tamaño={16} />
						{dias.length
							? '¿Ninguna hora te viene bien? Avísame cuando quede una'
							: 'Apuntarme a la lista de espera'}
					</button>
				{/if}
			{/if}
		{:else if paso === 'datos'}
			<button class="boton boton--suave boton--chico" type="button" onclick={() => (paso = 'hora')}>
				<Icon name="izquierda" tamaño={15} />Otra hora
			</button>
			<h2>
				<Icon name="calendario" tamaño={18} />{nombreDia(fecha ?? '')}, {hueco ? horaLarga(hueco.inicio) : ''}
			</h2>
			<p class="aviso">
				{servicio?.nombre} · {servicio?.duracion_minutos} min con {data.doctor.nombre}
			</p>

			<form class="tarjeta" onsubmit={(e) => { e.preventDefault(); confirmar(); }}>
				<div class="campo">
					<label class="etiqueta" for="nombre"><Icon name="personas" tamaño={14} />Nombre y apellidos</label>
					<input id="nombre" bind:value={nombre} autocomplete="name" required />
				</div>
				<div class="campo">
					<label class="etiqueta" for="telefono"><Icon name="telefono" tamaño={14} />Teléfono</label>
					<input id="telefono" type="tel" bind:value={telefono} autocomplete="tel" inputmode="tel" />
				</div>
				<div class="campo">
					<label class="etiqueta" for="correo"><Icon name="correo" tamaño={14} />Email (opcional)</label>
					<input id="correo" type="email" bind:value={correo} autocomplete="email" />
				</div>

				<label class="campo consentimiento" for="consentimiento">
					<input id="consentimiento" type="checkbox" bind:checked={consentimiento} />
					<span>
						Acepto que {data.doctor.nombre} guarde mi nombre, teléfono y esta cita para
						gestionar mi reserva, y que use el email que escriba solo para recordármela.
						No guardamos nada de tu salud: solo el tipo de visita.
						Puedes pedirle escribiéndole el archivo con tus datos, o que los borre.
					</span>
				</label>

				<button class="boton" type="submit" disabled={!puedeEnviar}>
					{#if guardando}
						<Icon name="recargar" tamaño={16} clase="giro" />Reservando…
					{:else}
						<Icon name="check" tamaño={16} />Confirmar reserva
					{/if}
				</button>

				{#if error}
					<p class="error" role="alert"><Icon name="alerta" tamaño={15} clase="ico ico--accion ico--nota" />{error}</p>
				{/if}
			</form>
			{#if data.avisos_de_email}
				{#if correo}
					<!-- El recordatorio es por email y solo si lo dejó: prometer un aviso que no puede
						llegar sería peor que no prometerlo. -->
					<p class="aviso aviso-recordatorio">
						<Icon name="campana" tamaño={15} clase="ico ico--accion ico--nota" />Te avisaremos a {correo} un día antes y otra vez una hora
						antes de la cita. Es automático, y puedes pedir que deje de llegarte escribiendo a
						{data.doctor.nombre}.
					</p>
				{:else}
					<p class="aviso aviso-recordatorio">
						<Icon name="campana" tamaño={15} clase="ico ico--accion ico--nota" />Si nos dejas un email arriba, te avisamos un día antes y
						una hora antes de la cita.
					</p>
				{/if}
				{#if terceros}
					<!-- RGPD: cuando el sobre no sale del correo de la consulta, el tercero se declara
						delante del paciente y no solo en el README. `avisos_de_email` ya garantiza que el
						canal está montado, así que aquí no hay promesa que sostener: solo un nombre. -->
					<p class="aviso aviso-tercero">
						Los avisos los manda {terceros} por encargo de {data.doctor.nombre}; solo le llega tu
						email y la hora de la cita.
					</p>
				{/if}
			{/if}
		{:else}
			<h2><Icon name="check" tamaño={18} />Cita reservada</h2>
			<div class="tarjeta resumen nace">
				<p class="hecha"><Icon name="diente" tamaño={17} /><strong>{cita?.servicio}</strong></p>
				<p>{nombreDia(cita?.fecha ?? '')} a las {cita ? horaLarga(cita.hora) : ''}</p>
				<p class="aviso">{cita?.duracion_minutos} min con {cita?.doctor}</p>
			</div>
			<button class="boton boton--suave" type="button" onclick={empezarDeNuevo}>
				<Icon name="diente" tamaño={16} />Reservar otra cosa
			</button>
		{/if}
	{/if}
</main>

<style>
	/* La placa de la consulta: la marca dibujada, el nombre y la promesa, centrados. Es lo primero
	   que ve quien llega desde un WhatsApp y no sabe qué le espera. */
	.placa {
		display: flex;
		flex-direction: column;
		align-items: center;
		text-align: center;
		gap: 0.2rem;
		margin: 0.5rem 0 1.25rem;
	}

	.placa h1 {
		margin: 0;
	}

	/* Un enlace roto es un aviso, no un título de bienvenida: va en rojo y con su icono. */
	.roto {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 1.3rem;
		color: var(--peligro);
	}

	.marca-diente {
		display: inline-flex;
		padding: 0.5rem;
		margin-bottom: 0.4rem;
		border-radius: 50%;
		color: var(--primario);
		background: var(--primario-suave);
	}

	.lista {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin-top: 1rem;
	}

	.opcion {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		width: 100%;
		text-align: left;
		cursor: pointer;
	}

	.opcion .nombre {
		flex: 1;
		font-weight: 600;
	}

	.dias {
		display: flex;
		gap: 0.4rem;
		overflow-x: auto;
		padding: 0.5rem 0 0.75rem;
		scrollbar-width: thin;
	}

	.dia {
		min-width: 3rem;
		padding: 0.5rem 0.25rem;
		border: 1px solid var(--borde);
		border-radius: 12px;
		background: var(--superficie);
		color: inherit;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.15rem;
		cursor: pointer;
	}

	.dia.activo {
		background: var(--primario);
		border-color: var(--primario);
		color: var(--primario-texto);
		box-shadow: 0 6px 14px -8px color-mix(in srgb, var(--primario) 70%, transparent);
	}

	.dow {
		font-size: 0.7rem;
		text-transform: uppercase;
		opacity: 0.75;
	}

	.num {
		font-size: 1.05rem;
		font-weight: 600;
	}

	.rejilla {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(4.5rem, 1fr));
		gap: 0.4rem;
	}

	.hora {
		min-height: var(--tap);
		padding: 0.5rem 0.25rem;
		border: 1px solid var(--borde);
		border-radius: 10px;
		background: var(--superficie);
		color: inherit;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		cursor: pointer;
	}

	.hora:hover {
		border-color: var(--primario);
		background: var(--primario-suave);
		color: var(--primario);
	}

	/* Los avisos con icono se alinean en dos columnas: la silueta a la izquierda y el texto al lado,
	   para que "15 min" o "quedan huecos" se lean con el mismo barrido que el resto de la página.
	   El icono lleva `clase="ico ico--accion"`: las utilidades están en styles.css, porque el CSS con
	   hash de Svelte no llega al SVG que renderiza Icon.svelte por dentro. */
	.dura,
	.dia-elegido,
	.aviso-recordatorio {
		display: flex;
		align-items: center;
		gap: 0.3rem;
	}

	.aviso-recordatorio {
		align-items: flex-start;
		line-height: 1.4;
		padding-left: 0.1rem;
	}

	/* La declaración del servicio de envíos va pegada a la promesa que declara, sangrada hasta donde
	   empieza su texto: la campana de arriba y dos líneas sueltas de abajo son la misma idea. */
	.aviso-recordatorio + .aviso-tercero {
		margin-top: -0.45rem;
		padding-left: 1.35rem;
		line-height: 1.4;
	}

	.consentimiento {
		flex-direction: row;
		align-items: flex-start;
		gap: 0.6rem;
		font-size: 0.85rem;
		line-height: 1.35rem;
	}

	.consentimiento input {
		margin-top: 0.2rem;
		width: 1.15rem;
		height: 1.15rem;
		accent-color: var(--primario);
	}

	.resumen p {
		margin: 0.25rem 0;
	}

	.titulo-lista {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin: 1.5rem 0 0.25rem;
		font-size: 1.05rem;
	}

	.enlace-lista {
		margin-top: 1.25rem;
	}

	.confirmada p {
		margin: 0.3rem 0;
	}
</style>
