/**
 * Iconografía propia de Agenda Solo: trazos SVG dibujados para esta app (diente, campana de
 * avisos, escudo de retención…), sin librería y sin descargar nada de un tercero.
 *
 * Todos los trazos están escritos sobre un viewBox de 24 con `fill: none` y `stroke:
 * currentColor`, así que el color lo manda el CSS del contenedor: el icono se apaga o se
 * ilumina con el propio texto del botón.
 *
 * El set es cerrado — las claves de `TRAZOS` son el único contenido que se inyecta en el DOM.
 * Ninguna entrada depende nunca del nombre de un paciente ni de un dato de la base.
 */
export const TRAZOS = {
	// La marca: corona ancha con dos raíces, dibujada en un solo trazo cerrado.
	diente: `<path pathLength="100" d="M7.3 4.3c-2.3 0-3.9 1.8-3.7 4.2.1 1.7 1 2.9 1.3 4.6.4 2.1.5 4.4 1.2 6.5.3 1 .9 1.7 1.6 1.7 1 0 1.3-1.3 1.6-2.9.3-1.7.7-3.5 2.2-3.5s1.9 1.8 2.2 3.5c.3 1.6.6 2.9 1.6 2.9.7 0 1.3-.7 1.6-1.7.7-2.1.8-4.4 1.2-6.5.3-1.7 1.2-2.9 1.3-4.6.2-2.4-1.4-4.2-3.7-4.2-1.6 0-2.5 1-4.2 1s-2.6-1-4.2-1Z"/>`,
	calendario: `<rect x="3.4" y="5.2" width="17.2" height="15.2" rx="3"/><path d="M3.4 10h17.2M8.2 3.2v3.8M15.8 3.2v3.8"/>`,
	reloj: `<circle cx="12" cy="12" r="8.4"/><path d="M12 7.3V12l3.4 2.3"/>`,
	campana: `<path d="M12 3.6v1.6"/><path d="M6.6 10.9a5.4 5.4 0 0 1 10.8 0c0 3.8 1 5.1 1.8 5.9H4.8c.8-.8 1.8-2.1 1.8-5.9Z"/><path d="M9.9 19.3a2.1 2.1 0 0 0 4.2 0"/>`,
	personas: `<circle cx="9.2" cy="8.6" r="3.4"/><path d="M3.8 19.6c0-3 2.4-5 5.4-5s5.4 2 5.4 5"/><path d="M16.4 6.4a3.4 3.4 0 0 1 0 6.5M17.6 15.2c2 .7 3.2 2.4 3.2 4.4"/>`,
	enlace: `<path d="M10.2 13.6a3.4 3.4 0 0 1 0-4.9l2.3-2.3a3.4 3.4 0 0 1 4.9 4.9l-1.1 1.1"/><path d="M13.8 10.4a3.4 3.4 0 0 1 0 4.9l-2.3 2.3a3.4 3.4 0 0 1-4.9-4.9l1.1-1.1"/>`,
	copiar: `<rect x="8.6" y="8.6" width="10.8" height="10.8" rx="2.6"/><path d="M15.6 5.6a2 2 0 0 0-2-2h-7a3 3 0 0 0-3 3v7a2 2 0 0 0 2 2"/>`,
	check: `<path d="M4.6 12.4l4.7 4.7L19.4 6.9"/>`,
	mas: `<path d="M12 5.6v12.8M5.6 12h12.8"/>`,
	menos: `<path d="M5.6 12h12.8"/>`,
	papelera: `<path d="M4.6 7.2h14.8M9.8 7.2V5.4a1.3 1.3 0 0 1 1.3-1.3h1.8a1.3 1.3 0 0 1 1.3 1.3v1.8"/><path d="M6.7 7.2l.8 11.4a2 2 0 0 0 2 1.8h5a2 2 0 0 0 2-1.8l.8-11.4"/>`,
	telefono: `<path d="M6.2 4.2h2.6l1.5 3.6-2 1.4a11.6 11.6 0 0 0 4.8 4.8l1.4-2 3.6 1.5v2.6a1.6 1.6 0 0 1-1.7 1.6c-6.4-.6-11-5.3-11.6-11.6a1.6 1.6 0 0 1 1.4-1.9Z"/>`,
	correo: `<rect x="3.2" y="5.6" width="17.6" height="12.8" rx="2.6"/><path d="M4.4 7.8 12 12.8l7.6-5"/>`,
	descargar: `<path d="M12 4.2v9.6M8.4 10.8 12 14.4l3.6-3.6"/><path d="M4.6 17v1.4a1.6 1.6 0 0 0 1.6 1.6h11.6a1.6 1.6 0 0 0 1.6-1.6V17"/>`,
	izquierda: `<path d="M14.4 5.8 8.2 12l6.2 6.2"/>`,
	derecha: `<path d="M9.6 5.8 15.8 12l-6.2 6.2"/>`,
	cerrar: `<path d="M6.6 6.6l10.8 10.8M17.4 6.6L6.6 17.4"/>`,
	alerta: `<path d="M12 4.6 3.2 19.6h17.6Z"/><path d="M12 9.6v4.1M12 16.5h.01"/>`,
	escudo: `<path d="M12 3.6 5.2 5.9v5.3c0 4.2 2.7 7.4 6.8 9.2 4.1-1.8 6.8-5 6.8-9.2V5.9Z"/><path d="M9.3 12.1 11.5 14.3l3.5-3.8"/>`,
	mensaje: `<path d="M20 11.8a8 8 0 0 1-11.9 7L4 20l1.3-3.9A8 8 0 1 1 20 11.8Z"/><path d="M8.7 11.2h6.6M8.7 8.5h6.6M8.7 13.9h4.1"/>`,
	recargar: `<path d="M19.4 12a7.4 7.4 0 1 1-2.8-5.8"/><path d="M19.6 4.4v3.9h-3.9"/>`,
	salir: `<path d="M14.6 4.4h3.2a2 2 0 0 1 2 2v11.2a2 2 0 0 1-2 2h-3.2"/><path d="M10.2 12H3.8M6.8 8.8 3.6 12l3.2 3.2"/>`,
	sol: `<circle cx="12" cy="12" r="4.2"/><path d="M12 3.4v2.1M12 18.5v2.1M3.4 12h2.1M18.5 12h2.1M5.9 5.9l1.5 1.5M16.6 16.6l1.5 1.5M18.1 5.9l-1.5 1.5M7.4 16.6l-1.5 1.5"/>`,
	// Disponibilidad: cada barra es una hora con hueco, más alta cuanto más libre la franja.
	hora_libre: `<path d="M4.4 16.6h15.2M7.8 16.6v-3.4M12 16.6V9.4M16.2 16.6v-5.2"/>`,
	gota: `<path d="M12 3.6c3.6 3.3 5.6 6 5.6 8.6a5.6 5.6 0 0 1-11.2 0c0-2.6 2-5.3 5.6-8.6Z"/>`,
} as const;

export type Icono = keyof typeof TRAZOS;
