<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import Agenda from '#lib/panel/Agenda.svelte';
	import Avisos from '#lib/panel/Avisos.svelte';
	import Horario from '#lib/panel/Horario.svelte';
	import ListaDeEspera from '#lib/panel/ListaDeEspera.svelte';
	import Retencion from '#lib/panel/Retencion.svelte';
	import Servicios from '#lib/panel/Servicios.svelte';
	import { sesion } from '#lib/sesion.svelte';

	const enlace = $derived(
		sesion.doctor ? `${window.location.origin}/d/${sesion.doctor.slug_unico}` : ''
	);

	// "Dra. Elena Ruiz Hoya" → "Elena": el tratamiento no es el nombre de pila.
	const nombre_pila = $derived(
		sesion.doctor?.nombre.split(/\s+/).find((p) => !/^(dr|dra|d)\.?$/i.test(p)) ?? ''
	);

	// El botón copió, pero el portapapeles no avisa: sin estado el clic era invisible. `error`
	// importa tanto como `ok` — negarlo dejaría al doctor enviando un enlace vacío.
	let copiado = $state<'ok' | 'error' | null>(null);
	let aviso_temporizador: ReturnType<typeof setTimeout> | undefined;

	async function copiar_enlace() {
		clearTimeout(aviso_temporizador);
		try {
			await navigator.clipboard.writeText(enlace);
			copiado = 'ok';
		} catch {
			copiado = 'error';
		}
		// La señal de "copiado" se queda lo que tarda en leerse y luego el botón vuelve a su sitio.
		// El error no se borra solo: si no copió, el aviso tiene que seguir delante.
		aviso_temporizador = setTimeout(() => {
			if (copiado === 'ok') copiado = null;
		}, 2600);
	}

	$effect(() => () => clearTimeout(aviso_temporizador));
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
			<span class="etiqueta"><Icon name="enlace" tamaño={15} /> Tu enlace de reservas</span>
			<code>{enlace}</code>
		</div>
		<button
			class="boton--suave boton--chico"
			class:ok={copiado === 'ok'}
			class:pulso={copiado === 'ok'}
			onclick={copiar_enlace}
		>
			{#if copiado === 'ok'}
				<Icon name="check" tamaño={16} /> Copiado
			{:else}
				<Icon name="copiar" tamaño={16} /> Copiar
			{/if}
		</button>
	</div>

	<!-- role=status: el navegador lee el cambio de "Copiar" a "Copiado" sin recargar la página. -->
	<p class="aviso" role="status" aria-live="polite">
		{#if copiado === 'error'}
			Tu navegador no permite copiar desde aquí. Selecciona el enlace, cópialo con Ctrl+C y pégalo
			donde lo necesites.
		{:else if copiado === 'ok'}
			Enlace copiado: pégalo en tu ficha, en tu WhatsApp o donde quieras.
		{:else}
			Pásale este enlace a tus pacientes: con él reservan solos.
		{/if}
	</p>
{/if}

<Agenda />
<Avisos />
<ListaDeEspera />
<Servicios />
<Horario />
<Retencion />

<style>
	code {
		word-break: break-all;
		font-size: 0.92rem;
	}
</style>
