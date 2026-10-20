<script lang="ts">
	import { TRAZOS, type Icono } from '#lib/icons';

	/**
	 * Un solo componente para toda la iconografía: `TRAZOS` es un conjunto cerrado de texto de
	 * este repo, así que lo que se inyecta nunca depende de un dato externo. El `aria-hidden`
	 * está activado porque todo icono va pegado a su etiqueta ("Copiar", "Hoy", "Servicios"):
	 * quien lee con lector de pantalla ya tiene el nombre, y el dibujo es decoración.
	 */
	let {
		name,
		tamaño = 20,
		clase = undefined as string | undefined,
		estilo = undefined as string | undefined
	}: { name: Icono; tamaño?: number; clase?: string; estilo?: string } = $props();
</script>

<svg
	viewBox="0 0 24 24"
	width={tamaño}
	height={tamaño}
	fill="none"
	stroke="currentColor"
	stroke-width="1.7"
	stroke-linecap="round"
	stroke-linejoin="round"
	class={clase}
	style={estilo}
	aria-hidden="true"
	focusable="false"
>
	{@html TRAZOS[name]}
</svg>
