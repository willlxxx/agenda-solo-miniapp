<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import { sesion } from '#lib/sesion.svelte';
	import type { Doctor, PerfilEdicion } from '#lib/types';

	// Las zonas donde está el público de estas agendas. Es autocompletado, no una lista cerrada: la
	// regla la pone el backend contra la base IANA, así que cualquier zona real se acepta igual.
	const HUSOS = [
		'America/Bogota',
		'America/Lima',
		'America/Mexico_City',
		'America/Monterrey',
		'America/Cancun',
		'America/Guatemala',
		'America/Tegucigalpa',
		'America/Managua',
		'America/San_Salvador',
		'America/Caracas',
		'America/La_Paz',
		'America/Santiago',
		'America/Asuncion',
		'America/Montevideo',
		'America/Buenos_Aires',
		'Europe/Madrid',
		'America/New_York'
	];

	let nombre = $state('');
	let correo = $state('');
	let telefono = $state('');
	let huso = $state('');
	let enlace = $state('');

	// La ficha tal y como está guardada: el parche se calcula contra ella, no contra lo que se fue
	// escribiendo, así que volver a dejar un campo como estaba no cuenta como cambio.
	let guardada = $state<Doctor | null>(null);

	$effect(() => {
		const doctor = sesion.doctor;
		// Se rellena solo al cambiar de cuenta. Después de guardar, `sesion.doctor` es un objeto nuevo
		// con el mismo id, y rellenar otra vez pisaría lo que el médico está tecleando.
		if (!doctor || doctor.id === guardada?.id) return;
		nombre = doctor.nombre;
		correo = doctor.email;
		telefono = doctor.telefono ?? '';
		huso = doctor.timezone;
		enlace = doctor.slug_unico;
		guardada = doctor;
	});

	function calcular_cambios(): PerfilEdicion {
		const base = guardada;
		if (!base) return {};
		const parche: PerfilEdicion = {};
		if (nombre.trim() !== base.nombre) parche.nombre = nombre.trim();
		if (correo.trim() !== base.email) parche.email = correo.trim();
		// Vaciar el teléfono es un cambio legítimo: la columna admite nulo y el panel deja de mostrarlo.
		const tel = telefono.trim();
		if (tel !== (base.telefono ?? '')) parche.telefono = tel === '' ? null : tel;
		if (huso.trim() !== base.timezone) parche.timezone = huso.trim();
		if (enlace.trim() !== base.slug_unico) parche.slug_unico = enlace.trim();
		return parche;
	}

	const cambios = $derived(calcular_cambios());
	const hay_cambios = $derived(Object.keys(cambios).length > 0);
	const completo = $derived(
		nombre.trim().length >= 2 && /\S+@\S+\.\S+/.test(correo.trim()) && huso.trim() !== '' && enlace.trim() !== ''
	);

	type Estado = 'quieto' | 'guardando' | 'guardado';
	let estado = $state<Estado>('quieto');
	let error = $state<string | null>(null);
	let aviso_temporizador: ReturnType<typeof setTimeout> | undefined;

	// La señal de "guardado" se apaga sola, como la del enlace copiado: si no, al minuto siguiente ya
	// no se sabe a qué guardado se refiere.
	function avisar_guardado() {
		clearTimeout(aviso_temporizador);
		aviso_temporizador = setTimeout(() => {
			if (estado === 'guardado') estado = 'quieto';
		}, 2600);
	}

	$effect(() => () => clearTimeout(aviso_temporizador));

	async function guardar(event: SubmitEvent) {
		event.preventDefault();
		if (!hay_cambios || !completo || estado === 'guardando') return;
		estado = 'guardando';
		error = null;
		try {
			const ficha = await sesion.actualizarPerfil(cambios);
			// Se pisan los campos con lo que devolvió el servidor: si normalizó el enlace
			// ("Mi Consulta" → "mi-consulta"), eso es lo que hay que ver y compartir.
			nombre = ficha.nombre;
			correo = ficha.email;
			telefono = ficha.telefono ?? '';
			huso = ficha.timezone;
			enlace = ficha.slug_unico;
			guardada = ficha;
			estado = 'guardado';
			avisar_guardado();
		} catch (e: unknown) {
			estado = 'quieto';
			error = e instanceof ErrorApi ? e.message : 'No se pudo guardar tu ficha';
		}
	}

	let actual = $state('');
	let nueva = $state('');
	let repetir = $state('');
	let estado_clave = $state<Estado>('quieto');
	let error_clave = $state<string | null>(null);

	// Pedir la nueva dos veces no es adorno: hoy no hay forma de recuperar la cuenta desde el propio
	// panel, y una clave escrita con errata en el último campo dejaría fuera a su dueño.
	const claves_listas = $derived(actual.length > 0 && nueva.length >= 8 && nueva === repetir);

	async function cambiar_clave(event: SubmitEvent) {
		event.preventDefault();
		if (!claves_listas || estado_clave === 'guardando') return;
		estado_clave = 'guardando';
		error_clave = null;
		try {
			await api.cambiarContrasena(actual, nueva);
			actual = '';
			nueva = '';
			repetir = '';
			estado_clave = 'guardado';
		} catch (e: unknown) {
			estado_clave = 'quieto';
			error_clave = e instanceof ErrorApi ? e.message : 'No se pudo cambiar la contraseña';
		}
	}
</script>

<section class="tarjeta">
	<h2><Icon name="diente" tamaño={19} clase="ico ico--accion" />Mi consulta</h2>
	<p class="aviso">
		Lo que ven tus pacientes en tu enlace y lo que usan los avisos automáticos. Nada se guarda hasta
		que pulses el botón.
	</p>

	{#if !guardada}
		<p class="aviso">Cargando tu ficha…</p>
	{:else}
		<form class="rejilla" onsubmit={guardar}>
			<div class="campo">
				<label class="etiqueta" for="consulta-nombre">
					<Icon name="personas" tamaño={15} />Nombre y apellidos
				</label>
				<input id="consulta-nombre" bind:value={nombre} autocomplete="name" required />
			</div>

			<div class="campo">
				<label class="etiqueta" for="consulta-correo">
					<Icon name="correo" tamaño={15} />Email de acceso
				</label>
				<input id="consulta-correo" type="email" bind:value={correo} autocomplete="email" required />
				<span class="aviso">Con este email entras al panel.</span>
			</div>

			<div class="campo">
				<label class="etiqueta" for="consulta-telefono">
					<Icon name="telefono" tamaño={15} />Teléfono (opcional)
				</label>
				<input id="consulta-telefono" type="tel" bind:value={telefono} inputmode="tel" />
				<span class="aviso">Para que te puedan contactar desde tu ficha pública.</span>
			</div>

			<div class="campo">
				<label class="etiqueta" for="consulta-huso">
					<Icon name="reloj" tamaño={15} />Zona horaria
				</label>
				<input id="consulta-huso" bind:value={huso} list="husos-consulta" spellcheck="false" required />
				<datalist id="husos-consulta">
					{#each HUSOS as zona}
						<option value={zona}></option>
					{/each}
				</datalist>
				<span class="aviso">
					Las citas que ya tienes guardadas mantienen su momento exacto: cambiar la zona solo cambia
					la hora a la que se leen en tu agenda.
				</span>
			</div>

			<div class="campo campo--ancho">
				<label class="etiqueta" for="consulta-enlace">
					<Icon name="enlace" tamaño={15} />Tu enlace de reservas
				</label>
				<div class="fila">
					<span class="prefijo" aria-hidden="true">/d/</span>
					<input
						id="consulta-enlace"
						bind:value={enlace}
						spellcheck="false"
						autocapitalize="none"
						required
					/>
				</div>
				<span class="aviso">
					Sin acentos ni espacios: se guarda como «mi-consulta». Si lo cambias, el enlace viejo deja
					de abrir tu ficha y toca pasar el nuevo.
				</span>
			</div>

			<div class="campo campo--ancho acciones">
				<button class="boton" type="submit" disabled={!hay_cambios || !completo || estado === 'guardando'}>
					Guardar cambios
				</button>
				{#if estado === 'guardando'}
					<span class="aviso fila"><Icon name="recargar" tamaño={15} clase="giro" />Guardando…</span>
				{:else if estado === 'guardado'}
					<span class="aviso hecha" role="status"><Icon name="check" tamaño={15} />Guardado</span>
				{:else if !hay_cambios}
					<span class="aviso">Nada por guardar.</span>
				{/if}
			</div>
		</form>

		{#if error}
			<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{error}</p>
		{/if}
	{/if}
</section>

<section class="tarjeta">
	<h2><Icon name="escudo" tamaño={19} clase="ico ico--accion" />Cambiar la contraseña</h2>
	<p class="aviso">
		Te pedimos la actual aunque ya estés dentro, para que una sesión abierta no bastase para cambiar
		la clave. Donde tengas la sesión abierta sigue entrando hasta que caduque: cerrar esas sesiones
		desde aquí todavía no se puede.
	</p>

	<form class="rejilla" onsubmit={cambiar_clave}>
		<div class="campo">
			<label class="etiqueta" for="clave-actual">Contraseña actual</label>
			<input
				id="clave-actual"
				type="password"
				bind:value={actual}
				autocomplete="current-password"
				oninput={() => (estado_clave = 'quieto')}
				required
			/>
		</div>

		<div class="campo">
			<label class="etiqueta" for="clave-nueva">Contraseña nueva</label>
			<input
				id="clave-nueva"
				type="password"
				bind:value={nueva}
				autocomplete="new-password"
				oninput={() => (estado_clave = 'quieto')}
				required
			/>
			<span class="aviso">Mínimo 8 caracteres.</span>
		</div>

		<div class="campo">
			<label class="etiqueta" for="clave-repetir">Repite la nueva</label>
			<input
				id="clave-repetir"
				type="password"
				bind:value={repetir}
				autocomplete="new-password"
				required
			/>
			{#if repetir !== '' && repetir !== nueva}
				<span class="aviso error">No coinciden.</span>
			{/if}
		</div>

		<div class="campo acciones">
			<button class="boton" type="submit" disabled={!claves_listas || estado_clave === 'guardando'}>
				Cambiar la contraseña
			</button>
			{#if estado_clave === 'guardando'}
				<span class="aviso fila"><Icon name="recargar" tamaño={15} clase="giro" />Guardando…</span>
			{:else if estado_clave === 'guardado'}
				<span class="aviso hecha" role="status"><Icon name="check" tamaño={15} />Contraseña cambiada</span>
			{/if}
		</div>
	</form>

	{#if error_clave}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{error_clave}</p>
	{/if}
</section>

<style>
	.rejilla {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
		gap: 0 1rem;
		margin-top: 0.5rem;
	}

	.campo--ancho {
		grid-column: 1 / -1;
	}

	/* El prefijo se pinta fuera del input: lo que se edita es solo la parte que es tuya. */
	.prefijo {
		font-family: var(--mono, ui-monospace, monospace);
		color: var(--texto-suave);
		padding: 0.5rem 0;
	}

	.acciones {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0.25rem;
	}
</style>
