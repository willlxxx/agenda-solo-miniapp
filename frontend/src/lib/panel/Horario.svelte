<script lang="ts">
	import { api, ErrorApi } from '#lib/api';
	import type { DiaBloqueado, Franja } from '#lib/types';

	const DIAS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

	let franjas = $state<Franja[]>([]);
	let bloqueados = $state<DiaBloqueado[]>([]);
	let mensaje = $state<string | null>(null);
	let nuevaFecha = $state('');
	let nuevoMotivo = $state('');

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
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo guardar la franja';
			await cargar(); // el servidor tiene la razón: recargamos tras un 409
		}
	}

	async function agregarFranja(dia: number) {
		mensaje = null;
		try {
			const creada = await api.crearFranja({
				dia_semana: dia,
				hora_inicio: '10:00',
				hora_fin: '11:00',
				activo: true
			});
			franjas = [...franjas, creada].sort((a, b) => a.dia_semana - b.dia_semana);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo añadir la franja';
		}
	}

	async function eliminarFranja(franja: Franja) {
		mensaje = null;
		try {
			await api.eliminarFranja(franja.id);
			franjas = franjas.filter((f) => f.id !== franja.id);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo eliminar la franja';
		}
	}

	async function bloquear(event: SubmitEvent) {
		event.preventDefault();
		if (!nuevaFecha) return;
		mensaje = null;
		try {
			const creado = await api.bloquear(nuevaFecha, nuevoMotivo.trim() || undefined);
			bloqueados = [...bloqueados, creado].sort((a, b) => a.fecha.localeCompare(b.fecha));
			nuevaFecha = '';
			nuevoMotivo = '';
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo bloquear el día';
		}
	}

	async function desbloquear(dia: DiaBloqueado) {
		mensaje = null;
		try {
			await api.desbloquear(dia.id);
			bloqueados = bloqueados.filter((d) => d.id !== dia.id);
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo desbloquear el día';
		}
	}
</script>

<section class="tarjeta">
	<h2>Horario semanal</h2>
	<p class="aviso">Toca una hora para cambiarla. Los huecos que ve tu paciente salen de aquí.</p>

	{#each DIAS as dia, i (dia)}
		<article class="dia">
			<div class="fila fila--entre">
				<strong>{dia}</strong>
				<button class="boton--suave boton--chico" onclick={() => agregarFranja(i)}>+ Franja</button>
			</div>

			{#if porDia[i].length === 0}
				<p class="aviso">Sin horas de consulta.</p>
			{/if}

			{#each porDia[i] as franja (franja.id)}
				<div class="fila franja" class:apagada={!franja.activo}>
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
					<button class="boton--peligro boton--chico" onclick={() => eliminarFranja(franja)}>Quitar</button>
				</div>
			{/each}
		</article>
	{/each}
</section>

<section class="tarjeta">
	<h2>Vacaciones y festivos</h2>
	<p class="aviso">El día completo queda cerrado para reservas.</p>

	<form class="fila" onsubmit={bloquear}>
		<!-- min en hoy: bloquear el pasado no tiene sentido -->
		<input type="date" min={hoyIso()} bind:value={nuevaFecha} aria-label="Fecha a bloquear" required />
		<input placeholder="Motivo (opcional)" bind:value={nuevoMotivo} aria-label="Motivo" />
		<button class="boton--suave boton--chico" type="submit">Bloquear</button>
	</form>

	{#if bloqueados.length === 0}
		<p class="aviso">Ningún día bloqueado.</p>
	{/if}

	<ul class="lista">
		{#each bloqueados as dia (dia.id)}
			<li class="fila fila--entre">
				<span>{dia.fecha}{dia.motivo ? ` · ${dia.motivo}` : ''}</span>
				<button class="boton--peligro boton--chico" onclick={() => desbloquear(dia)}>Desbloquear</button>
			</li>
		{/each}
	</ul>

	{#if mensaje}
		<p class="error" role="alert">{mensaje}</p>
	{/if}
</section>

<style>
	.dia {
		padding: 0.6rem 0;
		border-top: 1px solid var(--borde);
	}

	.franja {
		margin-top: 0.4rem;
	}

	.franja.apagada {
		opacity: 0.5;
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
