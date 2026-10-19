<script lang="ts">
	import { api, ErrorApi } from '#lib/api';
	import { panel } from '#lib/estado-panel.svelte';
	import { PREFERENCIAS } from '#lib/types';
	import type { EntradaLista, PreferenciaLista } from '#lib/types';

	type Filtro = 'pendientes' | 'contactadas' | 'todas';

	// Pedimos `todas` de una: la lista es corta y así las tres pestañas tienen su contador real
	// sin disparar tres peticiones cada vez que el doctor cambia de vista.
	let todas = $state<EntradaLista[]>([]);
	let filtro = $state<Filtro>('pendientes');
	let cargando = $state(true);
	let mensaje = $state<string | null>(null);
	let ocupada = $state<string | null>(null);
	// Borrar no se confirma con un diálogo del navegador (se puede bloquear): el botón pide un
	// segundo clic y el aviso se va solo al tocar cualquier otra fila.
	let a_borrar = $state<string | null>(null);

	const pendientes = $derived(todas.filter((e) => e.estado === 'en_espera'));
	const contactadas = $derived(todas.filter((e) => e.estado === 'contactada'));
	const visibles = $derived(
		filtro === 'pendientes' ? pendientes : filtro === 'contactadas' ? contactadas : todas
	);

	function etiqueta(p: PreferenciaLista | null): string {
		return PREFERENCIAS.find((x) => x.valor === p)?.texto ?? 'Cualquier hora';
	}

	/** '2026-10-26' → 'jue, 26 oct'. Mediodía local: el huso nunca mueve el día. */
	function dia(iso: string | null): string {
		if (!iso) return 'Cualquier día';
		return new Date(`${iso}T12:00:00`).toLocaleDateString('es-ES', {
			weekday: 'short',
			day: 'numeric',
			month: 'short'
		});
	}

	function pedido(e: EntradaLista): string {
		return new Date(e.pedido_utc).toLocaleString('es-ES', { day: 'numeric', month: 'short', hour: '2-digit' });
	}

	async function cargar() {
		cargando = true;
		mensaje = null;
		try {
			todas = (await api.listaEspera('todas')).entradas;
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo cargar la lista de espera';
			todas = [];
		} finally {
			cargando = false;
		}
	}

	$effect(() => {
		// `panel.version` sube cuando se guarda una cita desde otra sección (los avisos reservan por
		// el paciente): si no leyéramos la lista otra vez, la fila seguiría apareciendo como pendiente.
		if (panel.version >= 0) cargar();
	});

	async function marcar(entrada: EntradaLista, estado: 'en_espera' | 'contactada') {
		ocupada = entrada.id;
		a_borrar = null;
		mensaje = null;
		try {
			const actualizada = await api.marcarLista(entrada.id, estado);
			todas = todas.map((e) => (e.id === actualizada.id ? actualizada : e));
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo actualizar la petición';
			// Si el PATCH falló, la fila que vemos ya no es fiable: volvemos a leer la lista.
			await cargar();
		} finally {
			ocupada = null;
		}
	}

	async function quitar(entrada: EntradaLista) {
		if (a_borrar !== entrada.id) {
			a_borrar = entrada.id;
			return;
		}
		ocupada = entrada.id;
		a_borrar = null;
		mensaje = null;
		try {
			await api.quitarLista(entrada.id);
			// Retiramos la fila en local: recargar la lista entera por un borrón y una cuenta
			// sería tirar tres peticiones para quitar una línea.
			todas = todas.filter((e) => e.id !== entrada.id);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo quitar la petición';
			await cargar();
		} finally {
			ocupada = null;
		}
	}
</script>

<section class="tarjeta">
	<div class="fila fila--entre">
		<h2>Lista de espera</h2>
		{#if !cargando}
			<div class="filtros">
				<button class="filtro" class:activo={filtro === 'pendientes'} onclick={() => (filtro = 'pendientes')}>
					Pendientes ({pendientes.length})
				</button>
				<button class="filtro" class:activo={filtro === 'contactadas'} onclick={() => (filtro = 'contactadas')}>
					Avisados ({contactadas.length})
				</button>
				<button class="filtro" class:activo={filtro === 'todas'} onclick={() => (filtro = 'todas')}>
					Todas ({todas.length})
				</button>
			</div>
		{/if}
	</div>

	<p class="aviso">
		{#if cargando}
			Cargando…
		{:else if !todas.length}
			Nadie apuntado todavía. Cuando un paciente no encuentre hueco en tu enlace podrá dejar su
			teléfono aquí, y tú le llamas cuando liberes un día.
		{:else if !visibles.length}
			{filtro === 'pendientes' ? 'Nada pendiente: has avisado a todos.' : 'Sin resultados en esta vista.'}
		{:else}
			{visibles.length} petición{visibles.length === 1 ? '' : 'es'} en esta vista, por orden de llegada.
		{/if}
	</p>

	{#each visibles as entrada (entrada.id)}
		<article class="entrada" class:avisada={entrada.estado === 'contactada'}>
			<span class="nombre">{entrada.nombre}</span>
			{#if entrada.telefono}
				<a class="enlace" href="tel:{entrada.telefono}">{entrada.telefono}</a>
			{:else if entrada.email}
				<a class="enlace" href="mailto:{entrada.email}">{entrada.email}</a>
			{:else}
				<span class="aviso">sin contacto</span>
			{/if}
			<span class="dato">{entrada.servicio ?? 'Servicio quitado'}</span>
			<span class="dato">{dia(entrada.fecha)}</span>
			<span class="dato">{etiqueta(entrada.preferencia)}</span>
			<span class="aviso">pidió {pedido(entrada)}</span>

			{#if entrada.estado === 'en_espera'}
				<button class="boton--suave boton--chico" disabled={ocupada === entrada.id} onclick={() => marcar(entrada, 'contactada')}>
					Ya le avisé
				</button>
			{:else}
				<button class="boton--suave boton--chico" disabled={ocupada === entrada.id} onclick={() => marcar(entrada, 'en_espera')}>
					Reabrir
				</button>
			{/if}
			<button
				class="boton--peligro boton--chico"
				disabled={ocupada === entrada.id}
				onclick={() => quitar(entrada)}
			>
				{a_borrar === entrada.id ? 'Confirmar' : 'Quitar'}
			</button>
		</article>
	{/each}

	{#if mensaje}
		<p class="error" role="alert">{mensaje}</p>
	{/if}
</section>

<style>
	.filtros {
		display: flex;
		gap: 0.35rem;
		flex-wrap: wrap;
	}

	.filtro {
		min-height: var(--tap);
		padding: 0.3rem 0.7rem;
		border: 1px solid var(--borde);
		border-radius: 999px;
		background: var(--superficie);
		color: inherit;
		font-size: 0.85rem;
		cursor: pointer;
	}

	.filtro.activo {
		background: var(--primario);
		border-color: var(--primario);
		color: #fff;
	}

	.entrada {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem;
		padding: 0.5rem 0;
		border-top: 1px solid var(--borde);
	}

	.entrada.avisada {
		opacity: 0.6;
	}

	.nombre {
		font-weight: 600;
	}

	.dato {
		font-size: 0.9rem;
	}

	.enlace {
		color: var(--primario);
		text-decoration: underline;
	}
</style>
