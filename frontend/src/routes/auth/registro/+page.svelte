<script lang="ts">
	import { sesion } from '#lib/sesion.svelte';
	import { ErrorApi } from '#lib/api';

	let nombre = $state('');
	let email = $state('');
	let telefono = $state('');
	let clave = $state('');
	let guardando = $state(false);
	let error = $state<string | null>(null);

	const valido = $derived(
		nombre.trim().length >= 2 && /\S+@\S+\.\S+/.test(email) && clave.length >= 8
	);

	async function enviar(event: SubmitEvent) {
		event.preventDefault();
		if (!valido || guardando) return;
		guardando = true;
		error = null;
		try {
			await sesion.registrar({
				nombre: nombre.trim(),
				email: email.trim(),
				password: clave,
				telefono: telefono.trim() || undefined
			});
			// El onboarding mágico ya creó servicios y horario: vamos directos al panel.
			window.location.href = '/panel';
		} catch (e: unknown) {
			error = e instanceof ErrorApi ? e.message : 'No pudimos crear la cuenta. Inténtalo de nuevo.';
			guardando = false;
		}
	}
</script>

<svelte:head>
	<title>Crear cuenta · Agenda Solo</title>
</svelte:head>

<main class="contenedor">
	<a href="/">← Volver</a>
	<h1>Crea tu cuenta</h1>
	<p class="aviso">Con esto basta: tus 5 servicios y tu horario L-V se crean automáticamente.</p>

	<form class="tarjeta" onsubmit={enviar}>
		<div class="campo">
			<label class="etiqueta" for="nombre">Nombre y apellidos</label>
			<input id="nombre" bind:value={nombre} autocomplete="name" required />
		</div>

		<div class="campo">
			<label class="etiqueta" for="email">Email de acceso</label>
			<input id="email" type="email" bind:value={email} autocomplete="email" required />
		</div>

		<div class="campo">
			<label class="etiqueta" for="telefono">Teléfono (opcional)</label>
			<input id="telefono" type="tel" bind:value={telefono} autocomplete="tel" inputmode="tel" />
		</div>

		<div class="campo">
			<label class="etiqueta" for="clave">Contraseña</label>
			<input id="clave" type="password" bind:value={clave} autocomplete="new-password" required />
			<span class="aviso">Mínimo 8 caracteres.</span>
		</div>

		<button class="boton" type="submit" disabled={!valido || guardando}>
			{guardando ? 'Creando tu agenda…' : 'Crear mi cuenta'}
		</button>

		{#if error}
			<p class="error" role="alert">{error}</p>
		{/if}
	</form>

	<p class="aviso">
		¿Ya la tienes? <a href="/auth/acceso">Acceder</a>
	</p>
</main>
