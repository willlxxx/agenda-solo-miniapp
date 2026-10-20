<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { sesion } from '#lib/sesion.svelte';

	let children = $props();

	async function salir() {
		await sesion.salir();
		window.location.href = '/auth/acceso';
	}
</script>

<header class="barra">
	<div class="contenedor fila fila--entre">
		<strong class="marca"><Icon name="diente" tamaño={19} clase="ico ico--accion" /> Agenda Solo</strong>
		<div class="fila">
			<span class="aviso">{sesion.doctor?.nombre}</span>
			<button class="boton--suave boton--chico" onclick={salir}>
				<Icon name="salir" tamaño={15} /> Salir
			</button>
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

	/* La marca va en serif y con el diente delante: es el nombre de la consulta, no un menú. */
	.marca {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-family: var(--display);
		font-size: 1.05rem;
		letter-spacing: -0.01em;
	}

</style>
