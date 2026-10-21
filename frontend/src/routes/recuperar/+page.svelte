<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';

	// El enlace que llega por correo tiene la forma `/recuperar#token=…`. Se lee del fragmento y no de
	// la query a propósito: el fragmento no viaja al servidor, así que el token no acaba en los registros
	// de la API ni en el `Referer` de nadie. Aquí solo lo ve este script, en este navegador.
	let token = $state('');
	let sin_enlace = $state(false);

	onMount(() => {
		const crudo = window.location.hash.replace(/^#token=/, '');
		// Con 32 bytes aleatorios el token son 43 caracteres de [A-Za-z0-9_-]. Menos que eso es un
		// enlace cortado al copiar, y decirlo ahora ahorra el 400 genérico de más abajo.
		token = /^[A-Za-z0-9_-]{20,200}$/.test(crudo) ? crudo : '';
		sin_enlace = token === '';
	});

	let nueva = $state('');
	let repetir = $state('');
	let guardando = $state(false);
	let cambiada = $state(false);
	let error = $state<string | null>(null);

	const listas = $derived(nueva.length >= 8 && nueva === repetir);

	async function enviar(event: SubmitEvent) {
		event.preventDefault();
		if (!listas || guardando) return;
		guardando = true;
		error = null;
		try {
			await api.recuperarConfirmar(token, nueva);
			cambiada = true;
		} catch (e: unknown) {
			error = e instanceof ErrorApi ? e.message : 'No se pudo cambiar la contraseña.';
		} finally {
			guardando = false;
		}
	}
</script>

<svelte:head>
	<title>Recuperar el acceso · Agenda Solo</title>
</svelte:head>

<main class="contenedor">
	<h1>Poner una contraseña nueva</h1>

	{#if sin_enlace}
		<p class="error" role="alert">
			<Icon name="alerta" tamaño={15} />Este enlace llega sin token, así que no hay forma de saber de
			qué consulta es. Suele pasar al copiarlo a medias.
		</p>
		<p class="aviso">
			Vuelve a <a href="/auth/acceso">la pantalla de acceso</a> y pide uno desde «Pedir un enlace de
			acceso». Cada enlace vale una vez y caduca a los 30 minutos.
		</p>
	{:else if cambiada}
		<p class="hecha"><Icon name="check" tamaño={16} />Contraseña cambiada</p>
		<p>Ya puedes entrar con la nueva desde <a href="/auth/acceso">la pantalla de acceso</a>.</p>
		<p class="aviso">
			Este cambio no cerró las sesiones que ya tenías abiertas en otros aparatos: entran hasta que
			caduquen la cookie. Si alguna te preocupa, entra ahora y usa «Salir» desde ese aparato.
		</p>
	{:else}
		<p class="aviso">
			El enlace caduca a los 30 minutos y sirve para una sola vez, así que si lo abres desde un aparato
			que no es el tuyo, vuelve a tu correo y pide otro al entrar.
		</p>

		<form class="tarjeta" onsubmit={enviar}>
			<div class="campo">
				<label class="etiqueta" for="nueva">Contraseña nueva</label>
				<input
					id="nueva"
					type="password"
					bind:value={nueva}
					autocomplete="new-password"
					minlength={8}
					required
					oninput={() => (error = null)}
				/>
			</div>
			<div class="campo">
				<label class="etiqueta" for="repetir">Repite la contraseña nueva</label>
				<input
					id="repetir"
					type="password"
					bind:value={repetir}
					autocomplete="new-password"
					minlength={8}
					required
					oninput={() => (error = null)}
				/>
			</div>
			{#if repetir !== '' && !listas}
				<p class="aviso">Las dos contraseñas todavía no coinciden.</p>
			{/if}
			<button class="boton" type="submit" disabled={!listas || guardando}>
				{guardando ? 'Guardando…' : 'Guardar la contraseña'}
			</button>
			{#if error}
				<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{error}</p>
			{/if}
		</form>

		<p class="aviso">¿Prefieres no cambiar nada ahora? <a href="/auth/acceso">Volver a acceder</a></p>
	{/if}
</main>
