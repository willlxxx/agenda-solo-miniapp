<script lang="ts">
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

	/** Tres verdades distintas: alta nueva, repetida y pendiente, repetida y ya avisada. */
	const tituloLista = $derived.by(() => {
		if (!listaHecha) return '';
		if (listaHecha.creado) return 'Te hemos apuntado a la lista de espera';
		return listaHecha.estado === 'contactada'
			? 'Ya habías pedido ese día y tu médico ya te avisó'
			: 'Ya estabas en la lista de espera';
	});

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
		<h1>Enlace no válido</h1>
		<p class="aviso">
			Este enlace no corresponde a ninguna agenda. Pídele a tu médico que te vuelva a pasar su
			enlace.
		</p>
	{:else}
		<header>
			<h1>{data.doctor.nombre}</h1>
			<p class="aviso">Reserva tu cita. Sin registro y sin llamadas.</p>
		</header>

		{#if error && paso !== 'datos'}
			<p class="error" role="alert">{error}</p>
		{/if}

		{#if paso === 'servicio'}
			<h2>¿Qué necesitas?</h2>
			<div class="lista">
				{#each data.servicios as s (s.id)}
					<button class="tarjeta opcion" type="button" onclick={() => cargarHoras(s)}>
						<span class="punto punto--vivo" style="background:{s.color}"></span>
						<span class="nombre">{s.nombre}</span>
						<span class="aviso">{s.duracion_minutos} min</span>
					</button>
				{:else}
					<p class="aviso">Este médico aún no tiene servicios activos.</p>
				{/each}
			</div>
		{:else if paso === 'hora'}
			<button class="boton boton--suave boton--chico" type="button" onclick={() => (paso = 'servicio')}>
				← Otro servicio
			</button>
			<h2>{servicio?.nombre} · {servicio?.duracion_minutos} min</h2>

			{#if cargandoHoras}
				<p class="aviso">Mirando la agenda…</p>
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
				<p class="aviso">{nombreDia(fecha ?? '')}</p>

				<div class="rejilla">
					{#each huecosDelDia as h (h.inicio)}
						<button class="hora" type="button" onclick={() => elegirHueco(h)}>
							{horaLarga(h.inicio)}
						</button>
					{/each}
				</div>
			{:else if horizonte}
				<p class="aviso">
					No queda ningún hueco de {servicio?.nombre.toLowerCase()} entre el {diaCorto(horizonte.desde)} y el
					{diaCorto(horizonte.hasta)}.
				</p>
			{:else}
				<p class="aviso">Este médico no tiene horas libres ahora mismo.</p>
			{/if}

			{#if !cargandoHoras}
				{#if listaHecha}
					<div class="tarjeta confirmada">
						<p><strong>{tituloLista}</strong></p>
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
							Buscar otra cosa
						</button>
					</div>
				{:else if listaAbierta}
					<h3 class="titulo-lista">Avísame cuando quede un hueco</h3>
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
							{listaGuardando ? 'Apuntando…' : 'Apuntarme'}
						</button>

						{#if listaError}
							<p class="error" role="alert">{listaError}</p>
						{/if}
					</form>
				{:else}
					<button class="boton boton--suave enlace-lista" type="button" onclick={() => (listaAbierta = true)}>
						{dias.length
							? '¿Ninguna hora te viene bien? Avísame cuando quede una'
							: 'Apuntarme a la lista de espera'}
					</button>
				{/if}
			{/if}
		{:else if paso === 'datos'}
			<button class="boton boton--suave boton--chico" type="button" onclick={() => (paso = 'hora')}>
				← Otra hora
			</button>
			<h2>{nombreDia(fecha ?? '')}, {hueco ? horaLarga(hueco.inicio) : ''}</h2>
			<p class="aviso">
				{servicio?.nombre} · {servicio?.duracion_minutos} min con {data.doctor.nombre}
			</p>

			<form class="tarjeta" onsubmit={(e) => { e.preventDefault(); confirmar(); }}>
				<div class="campo">
					<label class="etiqueta" for="nombre">Nombre y apellidos</label>
					<input id="nombre" bind:value={nombre} autocomplete="name" required />
				</div>
				<div class="campo">
					<label class="etiqueta" for="telefono">Teléfono</label>
					<input id="telefono" type="tel" bind:value={telefono} autocomplete="tel" inputmode="tel" />
				</div>
				<div class="campo">
					<label class="etiqueta" for="correo">Email (opcional)</label>
					<input id="correo" type="email" bind:value={correo} autocomplete="email" />
				</div>

				<label class="campo consentimiento" for="consentimiento">
					<input id="consentimiento" type="checkbox" bind:checked={consentimiento} />
					<span>
						Acepto que {data.doctor.nombre} guarde mi nombre, teléfono y esta cita para
						gestionar mi reserva. No guardamos nada de tu salud: solo el tipo de visita.
						Puedes pedir que se borre escribiéndole.
					</span>
				</label>

				<button class="boton" type="submit" disabled={!puedeEnviar}>
					{guardando ? 'Reservando…' : 'Confirmar reserva'}
				</button>

				{#if error}
					<p class="error" role="alert">{error}</p>
				{/if}
			</form>
			<p class="aviso">Te recordamos la cita un día antes.</p>
		{:else}
			<h2>Cita reservada</h2>
			<div class="tarjeta resumen">
				<p><strong>{cita?.servicio}</strong></p>
				<p>{nombreDia(cita?.fecha ?? '')} a las {cita ? horaLarga(cita.hora) : ''}</p>
				<p class="aviso">{cita?.duracion_minutos} min con {cita?.doctor}</p>
			</div>
			<button class="boton boton--suave" type="button" onclick={empezarDeNuevo}>
				Reservar otra cosa
			</button>
		{/if}
	{/if}
</main>

<style>
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
		color: #fff;
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
