<script lang="ts">
	import Agenda from '#lib/panel/Agenda.svelte';
	import Horario from '#lib/panel/Horario.svelte';
	import ListaDeEspera from '#lib/panel/ListaDeEspera.svelte';
	import Servicios from '#lib/panel/Servicios.svelte';
	import { sesion } from '#lib/sesion.svelte';

	const enlace = $derived(
		sesion.doctor ? `${window.location.origin}/d/${sesion.doctor.slug_unico}` : ''
	);

	// "Dra. Elena Ruiz Hoya" → "Elena": el tratamiento no es el nombre de pila.
	const nombre_pila = $derived(
		sesion.doctor?.nombre.split(/\s+/).find((p) => !/^(dr|dra|d)\.?$/i.test(p)) ?? ''
	);
</script>

<svelte:head>
	<title>Mi agenda · Agenda Solo</title>
</svelte:head>

<h1>Hola, {nombre_pila}</h1>
<p class="aviso">
	Tu agenda ya está lista: revisa los servicios y el horario, y guarda esto para cuando abras tu
	ficha de Google Business.
</p>

{#if enlace}
	<div class="tarjeta fila fila--entre">
		<div>
			<span class="etiqueta">Tu enlace de reservas</span>
			<code>{enlace}</code>
		</div>
		<button
			class="boton--suave boton--chico"
			onclick={() => navigator.clipboard.writeText(enlace)}>Copiar</button>
	</div>
{/if}

<Agenda />
<ListaDeEspera />
<Servicios />
<Horario />

<style>
	code {
		word-break: break-all;
		font-size: 0.92rem;
	}
</style>
