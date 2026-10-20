<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import type { DiaBloqueado, Franja } from '#lib/types';

	const DIAS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

	let franjas = $state<Franja[]>([]);
	let bloqueados = $state<DiaBloqueado[]>([]);
	// Dos avisos separados: el de las franjas vive en la tarjeta del horario y el de los festivos en la
	// suya. Antes había uno solo dibujado abajo, así que un fallo al añadir una franja aparecía en la
	// tarjeta de vacaciones y el médico no veía nada donde había pulsado.
	let mensaje = $state<string | null>(null);
	let mensaje_vacaciones = $state<string | null>(null);
	let nuevaFecha = $state('');
	let nuevoMotivo = $state('');
	// Feedback de tres acciones que antes pasaban sin avisar: la franja que se está guardando
	// (botón ocupado), la que acaba de cambiar (destello) y la que se va (se apaga antes de salir).
	let añadiendo = $state<number | null>(null);
	let flash = $state<string | null>(null);
	let saliendo = $state<string | null>(null);
	let flash_temporizador: ReturnType<typeof setTimeout> | undefined;

	$effect(() => () => clearTimeout(flash_temporizador));

	const porDia = $derived(DIAS.map((_, dia) => franjas.filter((f) => f.dia_semana === dia)));

	async function cargar() {
		try {
			[franjas, bloqueados] = await Promise.all([api.franjas(), api.bloqueados()]);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo cargar el horario';
		}
	}

	$effect(() => {
		void cargar();
	});

	function hoyIso(): string {
		return new Date().toISOString().slice(0, 10);
	}

	async function cambiar(franja: Franja, parche: Partial<Franja>) {
		mensaje = null;
		try {
			const actualizada = await api.parchearFranja(franja.id, parche);
			franjas = franjas.map((f) => (f.id === actualizada.id ? actualizada : f));
			// El destello dice "esto se guardó", no solo "esto se movió": sin él, cambiar una hora
			// parece que no ha tocado nada hasta que el navegador pinta el input.
			flash = actualizada.id;
			clearTimeout(flash_temporizador);
			flash_temporizador = setTimeout(() => {
				if (flash === actualizada.id) flash = null;
			}, 460);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo guardar la franja';
			await cargar(); // el servidor tiene la razón: recargamos tras un 409
		}
	}

	// La franja nueva se coloca tras la última del día en lugar de una hora fija: con 10:00–11:00
	// siempre caía dentro de la mañana ya abierta y el botón devolvía un solape sin explicación.
	const ULTIMA_ENTRADA = 22; // una hora de 22:00 a 23:00 sigue siendo el último hueco razonable

	function hora_libre_despues(dia: number): number {
		const del_dia = franjas.filter((f) => f.dia_semana === dia);
		if (del_dia.length === 0) return 9;
		const ultima = del_dia.reduce((a, b) => (a.hora_fin >= b.hora_fin ? a : b));
		// Se salta los minutos sueltos: una franja que cierra a las 13:30 deja la siguiente a las 14:00.
		const h = Number(ultima.hora_fin.slice(0, 2));
		return Math.min(Number(ultima.hora_fin.slice(3, 5)) > 0 ? h + 1 : h, ULTIMA_ENTRADA);
	}

	async function agregarFranja(dia: number) {
		añadiendo = dia;
		mensaje = null;
		try {
			const inicio = hora_libre_despues(dia);
			const creada = await api.crearFranja({
				dia_semana: dia,
				hora_inicio: `${String(inicio).padStart(2, '0')}:00`,
				hora_fin: `${String(inicio + 1).padStart(2, '0')}:00`,
				activo: true
			});
			franjas = [...franjas, creada].sort((a, b) => a.dia_semana - b.dia_semana);
			flash = creada.id;
			clearTimeout(flash_temporizador);
			flash_temporizador = setTimeout(() => {
				if (flash === creada.id) flash = null;
			}, 460);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo añadir la franja';
		} finally {
			añadiendo = null;
		}
	}

	async function eliminarFranja(franja: Franja) {
		// La fila se apaga en cuanto se pulsa: si el borrado va lento, el médico ve que esa franja
		// ya está de salida en lugar de seguir tocando un horario que ya no existe.
		saliendo = franja.id;
		mensaje = null;
		try {
			await api.eliminarFranja(franja.id);
			franjas = franjas.filter((f) => f.id !== franja.id);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo eliminar la franja';
		} finally {
			saliendo = null;
		}
	}

	async function bloquear(event: SubmitEvent) {
		event.preventDefault();
		if (!nuevaFecha) return;
		mensaje_vacaciones = null;
		try {
			const creado = await api.bloquear(nuevaFecha, nuevoMotivo.trim() || undefined);
			bloqueados = [...bloqueados, creado].sort((a, b) => a.fecha.localeCompare(b.fecha));
			nuevaFecha = '';
			nuevoMotivo = '';
		} catch (error) {
			mensaje_vacaciones = error instanceof ErrorApi ? error.message : 'No se pudo bloquear el día';
		}
	}

	async function desbloquear(dia: DiaBloqueado) {
		mensaje_vacaciones = null;
		try {
			await api.desbloquear(dia.id);
			bloqueados = bloqueados.filter((d) => d.id !== dia.id);
		} catch (error) {
			mensaje_vacaciones = error instanceof ErrorApi ? error.message : 'No se pudo desbloquear el día';
		}
	}
</script>

<section class="tarjeta">
	<h2><Icon name="reloj" tamaño={19} clase="ico ico--accion" />Horario semanal</h2>
	<p class="aviso">Toca una hora para cambiarla. Los huecos que ve tu paciente salen de aquí.</p>

	{#each DIAS as dia, i (dia)}
		<article class="dia">
			<div class="fila fila--entre">
				<strong>{dia}</strong>
				<button
					class="boton--suave boton--chico"
					disabled={añadiendo === i}
					onclick={() => agregarFranja(i)}
				>
					<Icon name="mas" tamaño={15} />{añadiendo === i ? 'Añadiendo…' : 'Franja'}
				</button>
			</div>

			{#if porDia[i].length === 0}
				<p class="aviso">Sin horas de consulta.</p>
			{/if}

			{#each porDia[i] as franja (franja.id)}
				<!-- `nace` en la fila claveteada: la animación solo corre cuando la franja entra de
					verdad, no en cada guardado de una que ya estaba. -->
				<div
					class="fila franja nace"
					class:apagada={!franja.activo}
					class:cambia={flash === franja.id}
					class:sale={saliendo === franja.id}
				>
					<label class="ajuste">
						<input
							type="checkbox"
							checked={franja.activo}
							aria-label={`Activar ${dia}`}
							onchange={(e) => cambiar(franja, { activo: e.currentTarget.checked })}
						/>
					</label>
					<input
						type="time"
						class="hora"
						value={franja.hora_inicio.slice(0, 5)}
						aria-label={`Inicio ${dia}`}
						onchange={(e) => cambiar(franja, { hora_inicio: e.currentTarget.value })}
					/>
					<span class="aviso">a</span>
					<input
						type="time"
						class="hora"
						value={franja.hora_fin.slice(0, 5)}
						aria-label={`Fin ${dia}`}
						onchange={(e) => cambiar(franja, { hora_fin: e.currentTarget.value })}
					/>
					<!-- La palabra dice lo que la opacidad insinúa: sin texto, una franja apagada y una
						borrada se parecen demasiado. -->
					<span class="aviso">{franja.activo ? 'abierta' : 'cerrada'}</span>
					<button
						class="boton--peligro boton--chico"
						disabled={saliendo === franja.id}
						onclick={() => eliminarFranja(franja)}
					>
						<Icon name="papelera" tamaño={15} />{saliendo === franja.id ? 'Quitando…' : 'Quitar'}
					</button>
				</div>
			{/each}
		</article>
	{/each}

	{#if mensaje}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje}</p>
	{/if}
</section>

<section class="tarjeta">
	<h2><Icon name="sol" tamaño={19} clase="ico ico--accion" />Vacaciones y festivos</h2>
	<p class="aviso">El día completo queda cerrado para reservas.</p>

	<form class="fila" onsubmit={bloquear}>
		<!-- min en hoy: bloquear el pasado no tiene sentido -->
		<input type="date" min={hoyIso()} bind:value={nuevaFecha} aria-label="Fecha a bloquear" required />
		<input placeholder="Motivo (opcional)" bind:value={nuevoMotivo} aria-label="Motivo" />
		<button class="boton--suave boton--chico" type="submit"><Icon name="menos" tamaño={15} />Bloquear</button>
	</form>

	{#if bloqueados.length === 0}
		<p class="aviso">Ningún día bloqueado.</p>
	{/if}

	<ul class="lista">
		{#each bloqueados as dia (dia.id)}
			<li class="fila fila--entre nace">
				<span>{dia.fecha}{dia.motivo ? ` · ${dia.motivo}` : ''}</span>
				<button class="boton--suave boton--chico" onclick={() => desbloquear(dia)}><Icon name="mas" tamaño={15} />Desbloquear</button>
			</li>
		{/each}
	</ul>

	{#if mensaje_vacaciones}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje_vacaciones}</p>
	{/if}
</section>

<style>
	.dia {
		padding: 0.6rem 0;
		border-top: 1px solid var(--borde);
	}

	.franja {
		margin-top: 0.4rem;
		/* Sin transición, marcar la casquilla era un salto seco: el ojo no entiende que la franja
		   sigue ahí, solo que cerrada. */
		transition:
			opacity var(--dur) var(--curva),
			filter var(--dur) var(--curva);
	}

	.franja.apagada {
		opacity: 0.5;
		/* La opacidad suelta se lee como "fila deshabilitada del sistema"; el color apagado dice
		   que el día sigue abierto pero cerrado para reservar. */
		filter: saturate(0.5);
	}

	/* La franja que se está quitando: más apagada que una cerrada, porque ya no va a volver. */
	.franja.sale {
		opacity: 0.18;
	}

	.hora {
		width: 8rem;
		flex: none;
	}

	.lista {
		list-style: none;
		margin: 0.6rem 0 0;
		padding: 0;
	}

	.lista li {
		padding: 0.35rem 0;
		border-top: 1px solid var(--borde);
	}
</style>
