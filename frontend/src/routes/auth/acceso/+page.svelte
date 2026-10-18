<script lang="ts">
	import { sesion } from '#lib/sesion.svelte';
	import { ErrorApi } from '#lib/api';

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
</script>

<svelte:head>
	<title>Acceder · Agenda Solo</title>
</svelte:head>

<main class="contenedor">
	<a href="/">← Volver</a>
	<h1>Acceder a tu agenda</h1>

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
			<p class="error" role="alert">{error}</p>
		{/if}
	</form>

	<p class="aviso">
		¿Aún no tienes cuenta? <a href="/auth/registro">Crear mi cuenta</a>
	</p>
</main>
