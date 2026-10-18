<script lang="ts">
	import { sesion } from '#lib/sesion.svelte';

	let children = $props();

	async function salir() {
		await sesion.salir();
		window.location.href = '/auth/acceso';
	}
</script>

<header class="barra">
	<div class="contenedor fila fila--entre">
		<strong>Agenda Solo</strong>
		<div class="fila">
			<span class="aviso">{sesion.doctor?.nombre}</span>
			<button class="boton--suave boton--chico" onclick={salir}>Salir</button>
		</div>
	</div>
</header>

<main class="contenedor">
	{@render children.children()}
</main>

<style>
	.barra {
		position: sticky;
		top: 0;
		z-index: 10;
		background: color-mix(in srgb, var(--superficie) 92%, transparent);
		border-bottom: 1px solid var(--borde);
		backdrop-filter: blur(6px);
	}
</style>
