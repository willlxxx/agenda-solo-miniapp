<script lang="ts">
	import { api, ErrorApi } from '#lib/api';
	import type { CitaAgenda, EstadoCita } from '#lib/types';

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
		cargar(clave);
	});

	async function cambiar(cita: CitaAgenda, estado: EstadoCita) {
		ocupada = cita.id;
		mensaje = null;
		try {
			const actualizada = await api.cambiarEstado(cita.id, estado);
			citas = citas.map((c) => (c.id === actualizada.id ? actualizada : c));
			recientes = new Set([...recientes, actualizada.id]);
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

	function ya_paso(cita: CitaAgenda): boolean {
		return new Date(cita.inicio_utc) < new Date();
	}
</script>

<section class="tarjeta">
	<div class="fila fila--entre">
		<h2>Agenda</h2>
		<div class="fila">
			<button class="boton--suave boton--chico" onclick={() => paso(-1)} aria-label="Semana anterior">←</button>
			<span class="etiqueta">{etiqueta_semana()}</span>
			<button class="boton--suave boton--chico" onclick={() => paso(1)} aria-label="Semana siguiente">→</button>
			<button class="boton--suave boton--chico" onclick={() => (lunes = new Date(inicio_semana(Date.now())))}>
				Hoy
			</button>
		</div>
	</div>

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
					<article class="cita" class:cerrada={cita.estado !== 'confirmada'} class:past={ya_paso(cita)}>
						<span class="punto" style="background: {cita.color}" aria-hidden="true"></span>
						<span class="hora">{cita.hora.slice(0, 5)}–{cita.fin.slice(0, 5)}</span>
						<span class="qué">{cita.servicio ?? 'Servicio quitado'}</span>

						{#if cita.paciente}
							<span class="paciente">
								{cita.paciente.nombre}
								{#if cita.paciente.telefono}
									<a class="enlace" href="tel:{cita.paciente.telefono}">{cita.paciente.telefono}</a>
								{:else}
									<span class="aviso">sin teléfono</span>
								{/if}
							</span>
						{:else}
							<span class="aviso">paciente borrado</span>
						{/if}

						<span class="estado estado--{cita.estado}">{cita.estado}</span>

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
				{/each}
			{/if}
		</div>
	{/each}

	{#if mensaje}
		<p class="error" role="alert">{mensaje}</p>
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
		color: var(--primario);
		cursor: pointer;
		text-decoration: underline;
	}
</style>
