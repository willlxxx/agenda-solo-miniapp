<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import type { Retencion } from '#lib/types';

	// La banda 6-12 la fija el backend; aquí solo sirve para que el deslizador no ofrezca un número
	// que la API iba a rechazar.
	const MIN = 6;
	const MAX = 12;

	let datos = $state<Retencion | null>(null);
	let meses = $state(MAX);
	let cargando = $state(true);
	let guardando = $state(false);
	let guardado = $state(false);
	let mensaje = $state<string | null>(null);

	async function cargar() {
		cargando = true;
		try {
			// Se guarda en una variable local antes de asignarla: `datos` es estado, TypeScript no puede
			// saber que sigue intacta en la línea de después, y sin esto habría que estar comprobando nulos.
			const leido = await api.retencion();
			datos = leido;
			meses = leido.meses;
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo leer tu plazo de retención';
		} finally {
			cargando = false;
		}
	}

	async function guardar() {
		guardando = true;
		guardado = false;
		mensaje = null;
		try {
			const guardado_en_servidor = await api.guardarRetencion(meses);
			datos = guardado_en_servidor;
			// El servidor devuelve el plazo que guardó, no el que pedimos: si lo normalizó, se ve el suyo.
			meses = guardado_en_servidor.meses;
			guardado = true;
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo guardar el plazo';
		} finally {
			guardando = false;
		}
	}

	$effect(() => {
		cargar();
	});
</script>

<section class="tarjeta">
	<h2><Icon name="escudo" tamaño={19} clase="ico ico--accion" />Cuánto tiempo guardas las citas cerradas</h2>

	{#if cargando}
		<p class="aviso">Cargando…</p>
	{:else if datos}
		<!-- La frase sale del backend: describe la regla de borrado tal y como está escrita. -->
		<p class="aviso">{datos.explicacion}</p>

		<div class="control">
			<input
				type="range"
				min={MIN}
				max={MAX}
				step={1}
				bind:value={meses}
				onchange={guardar}
				aria-label="Meses de retención"
			/>
			<strong class="valor">{meses} {meses === 1 ? 'mes' : 'meses'}</strong>
		</div>
		<div class="extremos aviso">
			<span>{MIN} meses</span>
			<span>{MAX} meses</span>
		</div>

		<p class="aviso">
			Mover el número no borra nada en el momento: lo hace el repaso automático con el plazo que
			dejes puesto, y puedes volver atrás cuando quieras.
		</p>
	{:else}
		<p class="aviso">No hemos podido leer tu plazo. Recarga la página.</p>
	{/if}

	{#if mensaje}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje}</p>
	{:else if guardando}
		<p class="aviso fila"><Icon name="recargar" tamaño={15} clase="giro" />Guardando…</p>
	{:else if guardado}
		<p class="aviso hecha" role="status"><Icon name="check" tamaño={15} />Guardado: {meses} {meses === 1 ? 'mes' : 'meses'}</p>
	{/if}
</section>

<style>
	.control {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0.5rem;
	}

	input[type='range'] {
		flex: 1 1 10rem;
	}

	.valor {
		min-width: 5.5rem;
		text-align: right;
	}

	.extremos {
		display: flex;
		justify-content: space-between;
		margin-top: 0.25rem;
	}
</style>
