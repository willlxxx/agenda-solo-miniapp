<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { sesion } from '#lib/sesion.svelte';
	import { api, ErrorApi } from '#lib/api';
	import type { DisponibilidadRecuperacion } from '#lib/types';

	let email = $state('');
	let clave = $state('');
	let guardando = $state(false);
	let error = $state<string | null>(null);

	async function enviar(event: SubmitEvent) {
		event.preventDefault();
		if (guardando) return;
		guardando = true;
		error = null;
		try {
			await sesion.acceder(email.trim(), clave);
			window.location.href = '/panel';
		} catch (e: unknown) {
			error = e instanceof ErrorApi ? e.message : 'No pudimos acceder. Inténtalo de nuevo.';
			guardando = false;
		}
	}

	// La tarjeta de recuperación vive en la misma pantalla: quien olvidó la clave ya está aquí, y
	// mandarle a otra ruta no le quita ningún trabajo de encima.
	let recuperando = $state(false);
	let email_recuperar = $state('');
	let canal = $state<DisponibilidadRecuperacion | null>(null);
	let enviando = $state(false);
	let pedido = $state(false);
	let error_recuperacion = $state<string | null>(null);

	async function abrir_recuperacion() {
		recuperando = true;
		pedido = false;
		error_recuperacion = null;
		// El email ya tecleado pasa a la tarjeta: casi siempre se viene aquí tras escribirlo mal.
		email_recuperar = email.trim();
		if (canal) return;
		try {
			canal = await api.recuperarDisponible();
		} catch {
			// Si la pregunta falla no hay motivo para negar el intento: se deja pedir y el propio
			// intento devolverá lo que sea que esté pasando.
			canal = { disponible: true, razon: null };
		}
	}


	async function pedir_enlace(event: SubmitEvent) {
		event.preventDefault();
		if (enviando) return;
		enviando = true;
		error_recuperacion = null;
		try {
			await api.recuperarSolicitar(email_recuperar.trim());
			pedido = true;
		} catch (e: unknown) {
			error_recuperacion = e instanceof ErrorApi ? e.message : 'No se pudo preparar el enlace.';
		} finally {
			enviando = false;
		}
	}
</script>

<svelte:head>
	<title>Acceder · Agenda Solo</title>
</svelte:head>

<main class="contenedor">
	<a href="/">← Volver</a>
	<h1>Acceder a tu agenda</h1>

	{#if !recuperando}
		<form class="tarjeta" onsubmit={enviar}>
			<div class="campo">
				<label class="etiqueta" for="email">Email</label>
				<input id="email" type="email" bind:value={email} autocomplete="email" required />
			</div>
			<div class="campo">
				<label class="etiqueta" for="clave">Contraseña</label>
				<input id="clave" type="password" bind:value={clave} autocomplete="current-password" required />
			</div>
			<button class="boton" type="submit" disabled={guardando}>{guardando ? 'Entrando…' : 'Entrar'}</button>
			{#if error}
				<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{error}</p>
			{/if}
		</form>
	{:else if pedido}
		<div class="tarjeta">
			<p class="hecha"><Icon name="check" tamaño={16} />Petido el enlace</p>
			<p>
				Si <strong>{email_recuperar.trim()}</strong> tiene una agenda montada, llegará un correo con un
				enlace de un solo uso que caduca a los 30 minutos. Si no llega en unos minutos, revisa spam.
			</p>
			<p class="aviso">
				No decimos «te lo hemos mandado» porque no lo sabemos: el pedido se acepta igual exista tu
				cuenta o no, para que nadie pueda averiguar qué correos tienen una agenda.
			</p>
			<button class="boton boton--suave" type="button" onclick={() => (recuperando = false)}>
				Volver a acceder
			</button>
		</div>
	{:else}
		<form class="tarjeta" onsubmit={pedir_enlace}>
			<div class="campo">
				<label class="etiqueta" for="email-recuperar">Email de tu consulta</label>
				<input
					id="email-recuperar"
					type="email"
					bind:value={email_recuperar}
					autocomplete="email"
					required
				/>
			</div>

			{#if canal && !canal.disponible}
				<p class="error" role="alert">
					<Icon name="alerta" tamaño={15} />Este despliegue todavía no puede mandar correos, así que no
					hay enlace que puedas pedir. Si tienes la sesión abierta en otro aparato, cambia la contraseña
					desde <strong>Mi consulta</strong> del panel.
				</p>
			{:else}
				{#if canal?.razon === 'simulado_en_desarrollo'}
					<p class="aviso">
						Canal simulado: el enlace no sale por correo, se imprime en la consola del servidor. Es el
						modo de desarrollo, no el de tus pacientes.
					</p>
				{/if}
				<button class="boton" type="submit" disabled={enviando}>
					{enviando ? 'Enviando…' : 'Enviarme el enlace'}
				</button>
				{#if error_recuperacion}
					<p class="error" role="alert">
						<Icon name="alerta" tamaño={15} />{error_recuperacion}
					</p>
				{/if}
			{/if}
		</form>
	{/if}

	{#if !recuperando}
		<p class="aviso">
			¿Aún no tienes cuenta? <a href="/auth/registro">Crear mi cuenta</a>
		</p>
		<p class="aviso">
			¿No la recuerdas? <button class="enlace" type="button" onclick={abrir_recuperacion}>
				Pedir un enlace de acceso
			</button>
		</p>
	{:else}
		<p class="aviso">
			¿Recordaste la clave? <button class="enlace" type="button" onclick={() => (recuperando = false)}>
				Volver a acceder
			</button>
		</p>
	{/if}
</main>

<style>
	/* Un botón que se ve como enlace, para una acción que no navega: cambiar de tarjeta en la misma
	   pantalla. Con `<a>` haría falta un href que no existe, y un enlace sin URL engaña al navegador. */
	.enlace {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: var(--primario);
		cursor: pointer;
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.enlace:hover {
		text-decoration-thickness: 2px;
	}
</style>
