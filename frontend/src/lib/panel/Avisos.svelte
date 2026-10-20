<script lang="ts">
	import Icon from '#lib/Icon.svelte';
	import { api, ErrorApi } from '#lib/api';
	import { panel } from '#lib/estado-panel.svelte';
	import { PREFERENCIAS } from '#lib/types';
	import type { AvisoHueco, AvisosHueco, PreferenciaLista } from '#lib/types';

	// Avisos de hueco: la lista de espera cruzada con la disponibilidad REAL de hoy.
	// Aquí no sale ningún mensaje: se abre el WhatsApp o el correo de la cuenta del médico, y él
	// decide si lo envía. Reservar por él escribe la cita con el mismo candado que el enlace público.

	let resumen = $state<AvisosHueco | null>(null);
	let avisos = $state<AvisoHueco[]>([]);
	let cargando = $state(true);
	let ocupada = $state<string | null>(null);
	let mensaje = $state<string | null>(null);
	// El aviso de "cita guardada" es positivo y se queda hasta la siguiente acción: si no, el 409
	// de otro paciente lo borraría de la pantalla y el médico no sabría que una sí cuajó.
	let guardado = $state<string | null>(null);
	let copiado = $state<string | null>(null);

	const sin_hueco = $derived(resumen === null ? 0 : resumen.pendientes - resumen.con_hueco);

	function franja(p: PreferenciaLista | null): string {
		return PREFERENCIAS.find((x) => x.valor === p)?.texto ?? 'Cualquier hora';
	}

	/** '2026-10-26' → 'lun, 26 oct'. Al mediodía local: el huso nunca desplaza el día. */
	function dia(iso: string): string {
		return new Date(`${iso}T12:00:00`).toLocaleDateString('es-ES', {
			weekday: 'short',
			day: 'numeric',
			month: 'short'
		});
	}

	function hora(texto: string): string {
		return texto.slice(0, 5);
	}

	/** Por qué este hueco es suyo: el día y la franja que marcó al apuntarse. */
	function pidio(aviso: AvisoHueco): string {
		const cuando = aviso.pidio_fecha ? dia(aviso.pidio_fecha) : 'cualquier día';
		return `pidió ${cuando}, ${franja(aviso.preferencia).toLowerCase()}`;
	}

	async function cargar() {
		cargando = true;
		mensaje = null;
		copiado = null;
		try {
			resumen = await api.encajes();
			avisos = resumen.avisos;
		} catch (error) {
			mensaje = error instanceof ErrorApi ? error.message : 'No se pudo cruzar la lista con tu agenda';
			resumen = null;
			avisos = [];
		} finally {
			cargando = false;
		}
	}

	$effect(() => {
		cargar();
	});

	async function copiar(aviso: AvisoHueco) {
		mensaje = null;
		try {
			await navigator.clipboard.writeText(aviso.mensaje);
			copiado = aviso.id;
		} catch {
			mensaje = 'Tu navegador no permite copiar desde aquí: selecciona el texto y cópialo a mano.';
		}
	}

	async function reservar(aviso: AvisoHueco) {
		ocupada = aviso.id;
		mensaje = null;
		guardado = null;
		try {
			const hecha = await api.reservarPorEl(aviso.id, { fecha: aviso.fecha, hora: aviso.inicio });
			// La quitamos de la oferta: la cita ya está escrita y la fila quedó como avisada.
			avisos = avisos.filter((a) => a.id !== aviso.id);
			if (resumen !== null) resumen = { ...resumen, con_hueco: avisos.length, pendientes: resumen.pendientes - 1 };
			guardado = `Cita guardada para ${hecha.paciente}: ${dia(hecha.fecha)}, ${hora(hecha.hora)}–${hora(hecha.fin)}.`;
			// Agenda y lista de espera leen la misma base: hay que volver a por ellas.
			panel.avisar();
		} catch (error) {
			const texto = error instanceof ErrorApi ? error.message : 'No se pudo guardar la cita';
			// 409: alguien ocupó el hueco por el enlace mientras se escribía el aviso. Recargamos la
			// oferta para no seguir proponiendo algo que ya no existe.
			if (error instanceof ErrorApi && error.status === 409) await cargar();
			mensaje = texto;
		} finally {
			ocupada = null;
		}
	}
</script>

<section class="tarjeta">
	<div class="fila fila--entre">
		<h2><Icon name="campana" tamaño={19} clase="ico ico--accion" />Huecos para quien espera</h2>
		<button class="boton--suave boton--chico" disabled={cargando} onclick={() => cargar()}>
			<Icon name="recargar" tamaño={15} clase={cargando ? 'giro' : undefined} />
			{cargando ? 'Revisando…' : 'Revisar otra vez'}
		</button>
	</div>

	<p class="aviso">
		{#if cargando}
			Cruzando la lista con tu agenda…
		{:else if !avisos.length}
			{#if resumen === null}
				Sin avisos ahora mismo.
			{:else if resumen.pendientes === 0}
				Nadie esperando: la lista está vacía.
			{:else}
				{sin_hueco} petición{sin_hueco === 1 ? '' : 'es'} pendiente{sin_hueco === 1 ? '' : 's'} sin hueco libre
				que ofrecerle. Abre una franja en tu horario y vuelve a revisar: en cuanto exista hora, saldrá aquí.
			{/if}
		{:else}
			{avisos.length} petición{avisos.length === 1 ? '' : 'es'} con hueco que ofrecer, por orden de llegada.
			Ningún mensaje sale solo: el WhatsApp o el correo los envías tú.
			{#if sin_hueco > 0}
				· {sin_hueco} sigue(n) esperando.
			{/if}
		{/if}
	</p>

	{#each avisos as aviso (aviso.id)}
		<article class="oferta">
			<div class="fila fila--entre">
				<span class="nombre">{aviso.nombre}</span>
				<span class="hora">{dia(aviso.fecha)} · {hora(aviso.inicio)}–{hora(aviso.fin)}</span>
			</div>
			<p class="aviso">
				{aviso.servicio ?? 'Servicio quitado'} · {pidio(aviso)}
				{#if aviso.telefono}· <a class="enlace" href="tel:{aviso.telefono}">{aviso.telefono}</a>{/if}
			</p>
			<p class="texto">{aviso.mensaje}</p>

			<div class="fila">
				{#if aviso.whatsapp}
					<a
						class="boton boton--chico enlace-boton"
						href={aviso.whatsapp}
						target="_blank"
						rel="noopener noreferrer"
						><Icon name="mensaje" tamaño={15} />WhatsApp</a
					>
				{:else}
					<span class="aviso">sin WhatsApp: teléfono no internacional</span>
				{/if}
				{#if aviso.correo}
					<a class="boton--suave boton--chico enlace-boton" href={aviso.correo}><Icon name="correo" tamaño={15} />Correo</a>
				{/if}
				<!-- El botón cambia de color y de palabra a la vez: el portapapeles no devuelve aviso. -->
				<button
					class="boton--suave boton--chico"
					class:ok={copiado === aviso.id}
					class:pulso={copiado === aviso.id}
					onclick={() => copiar(aviso)}
				>
					<Icon name={copiado === aviso.id ? 'check' : 'copiar'} tamaño={15} />
					{copiado === aviso.id ? 'Copiado' : 'Copiar mensaje'}
				</button>
				<button
					class="boton boton--chico"
					disabled={ocupada === aviso.id}
					onclick={() => reservar(aviso)}
				>
					{#if ocupada === aviso.id}
						<Icon name="recargar" tamaño={15} clase="giro" />
					{:else}
						<Icon name="calendario" tamaño={15} />
					{/if}
					{ocupada === aviso.id ? 'Guardando…' : 'Reservar por él'}
				</button>
			</div>
		</article>
	{/each}

	{#if guardado}
		<p class="aviso hecha" role="status"><Icon name="check" tamaño={15} />{guardado}</p>
	{/if}
	{#if mensaje}
		<p class="error" role="alert"><Icon name="alerta" tamaño={15} />{mensaje}</p>
	{/if}
</section>

<style>
	.oferta {
		padding: 0.7rem 0;
		border-top: 1px solid var(--borde);
	}

	.nombre {
		font-weight: 600;
	}

	.hora {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
	}

	.texto {
		margin: 0.4rem 0 0.6rem;
		padding: 0.5rem 0.6rem;
		border: 1px solid var(--borde);
		border-radius: 10px;
		background: var(--superficie);
		font-size: 0.9rem;
	}

	.enlace-boton {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		text-decoration: none;
		border: 1px solid var(--borde);
		border-radius: 10px;
	}

	.enlace {
		color: var(--primario);
		text-decoration: underline;
	}
</style>
