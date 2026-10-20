<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import type { Servicio } from '#lib/types';

	type Estado = 'cargando' | 'listo' | 'guardando' | 'guardado' | 'error';

	let servicios = $state<Servicio[]>([]);
	let estado = $state<Estado>('cargando');
	let mensaje = $state<string | null>(null);
	let nuevoNombre = $state('');
	let nuevaDuracion = $state(30);

	// El autoguardado no tiene botón: la prueba de que llegó al servidor es esta luz en la fila.
	let recién = $state<string | null>(null);
	let recién_temporizador: ReturnType<typeof setTimeout> | undefined;

	function avisar_guardado(id: string) {
		recién = id;
		clearTimeout(recién_temporizador);
		recién_temporizador = setTimeout(() => {
			if (recién === id) recién = null;
		}, 460);
	}

	$effect(() => () => clearTimeout(recién_temporizador));

	const temporizadores = new Map<string, ReturnType<typeof setTimeout>>();

	/** Autoguardado: sin botón "Guardar", pero sin bombardear la API en cada tecla. */
	function autoguardar(servicio: Servicio, parche: Partial<Servicio>) {
		Object.assign(servicio, parche);
		const anterior = temporizadores.get(servicio.id);
		if (anterior) clearTimeout(anterior);
		estado = 'guardando';
		temporizadores.set(
			servicio.id,
			setTimeout(async () => {
				try {
					const actualizado = await api.parchearServicio(servicio.id, parche);
					servicios = servicios.map((s) => (s.id === actualizado.id ? actualizado : s));
					estado = 'guardado';
					mensaje = null;
					avisar_guardado(actualizado.id);
				} catch (error) {
					estado = 'error';
					mensaje = error instanceof ErrorApi ? error.message : 'No se pudo guardar';
				}
			}, 400)
		);
	}

	async function cargar() {
		estado = 'cargando';
		try {
			servicios = await api.servicios();
			estado = 'listo';
		} catch (error) {
			estado = 'error';
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudieron cargar los servicios';
		}
	}

	$effect(() => {
		cargar();
		return () => temporizadores.forEach(clearTimeout);
	});

	function ajustarDuracion(servicio: Servicio, delta: number) {
		const proposed = Math.min(240, Math.max(5, servicio.duracion_minutos + delta));
		if (proposed !== servicio.duracion_minutos) autoguardar(servicio, { duracion_minutos: proposed });
	}

	async function cambiarActivo(servicio: Servicio, activo: boolean) {
		autoguardar(servicio, { activo });
	}

	async function desactivarDefinitivo(servicio: Servicio) {
		try {
			await api.desactivarServicio(servicio.id);
			servicios = servicios.map((s) => (s.id === servicio.id ? { ...s, activo: false } : s));
			estado = 'guardado';
			avisar_guardado(servicio.id);
		} catch (error) {
			estado = 'error';
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo desactivar';
		}
	}

	async function agregar(event: SubmitEvent) {
		event.preventDefault();
		if (!nuevoNombre.trim()) return;
		try {
			const creado = await api.crearServicio({
				nombre: nuevoNombre.trim(),
				duracion_minutos: nuevaDuracion,
				orden: servicios.length
			});
			servicios = [...servicios, creado];
			nuevoNombre = '';
			nuevaDuracion = 30;
			estado = 'guardado';
			avisar_guardado(creado.id);
		} catch (error) {
			estado = 'error';
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo crear el servicio';
		}
	}

	const activos = $derived(servicios.filter((s) => s.activo).length);
</script>

<section class="tarjeta">
	<div class="fila fila--entre">
		<h2><Icon name="diente" tamaño={19} clase="ico ico--accion" />Servicios</h2>
		<span class="aviso">{activos} activos</span>
	</div>
	<p class="aviso">
		Ya vienen con duraciones típicas. Cámbialas solo si no te encajan: se guardan solas.
	</p>

	{#if estado === 'cargando'}
		<p class="aviso">Cargando…</p>
	{:else if servicios.length === 0}
		<p class="aviso">No tienes servicios todavía. Añade el primero abajo.</p>
	{/if}

	{#each servicios as servicio (servicio.id)}
		<!-- `nace`: la animación solo corre cuando el servicio entra en la lista, no en cada tecla. -->
		<article
			class="servicio nace"
			class:apagado={!servicio.activo}
			class:cambia={recién === servicio.id}
		>
			<span class="punto" style="background: {servicio.color}" aria-hidden="true"></span>

			<input
				class="nombre"
				aria-label={`Nombre de ${servicio.nombre}`}
				value={servicio.nombre}
				oninput={(e) => autoguardar(servicio, { nombre: e.currentTarget.value })}
			/>

			<div class="ajuste">
				<button class="boton--suave boton--chico" aria-label="Restar 5 minutos" onclick={() => ajustarDuracion(servicio, -5)}>
					<Icon name="menos" tamaño={14} />5
				</button>
				<span>{servicio.duracion_minutos} min</span>
				<button class="boton--suave boton--chico" aria-label="Sumar 5 minutos" onclick={() => ajustarDuracion(servicio, 5)}>
					<Icon name="mas" tamaño={14} />5
				</button>
			</div>

			<label class="ajuste">
				<input
					type="checkbox"
					checked={servicio.activo}
					onchange={(e) => cambiarActivo(servicio, e.currentTarget.checked)}
				/>
				<span class="aviso">{servicio.activo ? 'visible' : 'oculto'}</span>
			</label>

			<button class="boton--peligro boton--chico" onclick={() => desactivarDefinitivo(servicio)}>
				<Icon name="papelera" tamaño={15} />Ocultar
			</button>
		</article>
	{/each}

	<form class="fila" onsubmit={agregar}>
		<input placeholder="Otro servicio (p. ej. Ortodoncia)" bind:value={nuevoNombre} aria-label="Nuevo servicio" />
		<input class="duracion" type="number" min="5" max="240" step="5" bind:value={nuevaDuracion} aria-label="Duración en minutos" />
		<span class="aviso">min</span>
		<button class="boton--suave boton--chico" type="submit"><Icon name="mas" tamaño={15} />Añadir</button>
	</form>

	{#if mensaje}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje}</p>
	{:else if estado === 'guardando'}
		<p class="aviso">Guardando…</p>
	{:else if estado === 'guardado'}
		<p class="aviso hecha" role="status"><Icon name="check" tamaño={15} />Guardado</p>
	{/if}
</section>

<style>
	.servicio {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--borde);
		flex-wrap: wrap;
	}

	.servicio.apagado {
		opacity: 0.5;
	}

	.nombre {
		flex: 1 1 12rem;
	}

	.duracion {
		width: 5.5rem;
		flex: none;
	}
</style>
