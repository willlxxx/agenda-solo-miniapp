import type {
	AgendaVentana,
	Cita,
	CitaAgenda,
	DiaBloqueado,
	Disponibilidad,
	Doctor,
	EstadoCita,
	FichaPublica,
	Franja,
	Servicio
} from '#lib/types';

const BASE = '/api/v1';

export class ErrorApi extends Error {
	constructor(
		readonly status: number,
		message: string
	) {
		super(message);
	}
}

async function request<T>(ruta: string, opciones: RequestInit = {}): Promise<T> {
	const resp = await fetch(`${BASE}${ruta}`, {
		credentials: 'same-origin',
		...opciones,
		headers: {
			'Content-Type': 'application/json',
			...(opciones.headers ?? {})
		}
	});

	if (resp.status === 204) return undefined as T;

	if (!resp.ok) {
		let detalle = `Error ${resp.status}`;
		try {
			const cuerpo = await resp.json();
			if (typeof cuerpo?.detail === 'string') detalle = cuerpo.detail;
			else if (Array.isArray(cuerpo?.detail)) detalle = cuerpo.detail[0]?.msg ?? detalle;
		} catch {
			/* respuesta sin JSON: quedamos con el status */
		}
		throw new ErrorApi(resp.status, detalle);
	}
	return resp.json() as Promise<T>;
}

const json = (datos: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(datos) });

export const api = {
	registro: (datos: object) => request<Doctor>('/auth/registro', json(datos)),
	acceso: (email: string, password: string) =>
		request<Doctor>('/auth/acceso', json({ email, password })),
	salida: () => request<void>('/auth/salida', { method: 'POST' }),
	yo: () => request<Doctor>('/auth/yo'),

	servicios: () => request<Servicio[]>('/services'),
	crearServicio: (datos: object) => request<Servicio>('/services', json(datos)),
	parchearServicio: (id: string, datos: object) =>
		request<Servicio>(`/services/${id}`, { method: 'PATCH', body: JSON.stringify(datos) }),
	desactivarServicio: (id: string) => request<void>(`/services/${id}`, { method: 'DELETE' }),

	franjas: () => request<Franja[]>('/availability'),
	crearFranja: (datos: object) => request<Franja>('/availability', json(datos)),
	parchearFranja: (id: string, datos: object) =>
		request<Franja>(`/availability/${id}`, { method: 'PATCH', body: JSON.stringify(datos) }),
	eliminarFranja: (id: string) => request<void>(`/availability/${id}`, { method: 'DELETE' }),

	bloqueados: () => request<DiaBloqueado[]>('/blocked-dates'),
	bloquear: (fecha: string, motivo?: string) =>
		request<DiaBloqueado>('/blocked-dates', json({ fecha, motivo: motivo ?? null })),
	desbloquear: (id: string) => request<void>(`/blocked-dates/${id}`, { method: 'DELETE' }),

	// Enlace público del paciente: sin cookie, y la hora viaja en el huso del doctor.
	ficha: (slug: string) => request<FichaPublica>(`/public/${encodeURIComponent(slug)}`),
	disponibilidad: (slug: string, servicio: string, dias = 14) =>
		request<Disponibilidad>(
			`/public/${encodeURIComponent(slug)}/disponibilidad?servicio=${servicio}&dias=${dias}`
		),
	reservar: (slug: string, datos: object) => request<Cita>(`/public/${encodeURIComponent(slug)}/citas`, json(datos)),

	// Agenda del doctor: la ventana pedida, pintada en su huso, y el estado de cada cita.
	agenda: (desde: string, dias = 7) => request<AgendaVentana>(`/appointments?desde=${desde}&dias=${dias}`),
	cambiarEstado: (id: string, estado: EstadoCita) =>
		request<CitaAgenda>(`/appointments/${id}`, { method: 'PATCH', body: JSON.stringify({ estado }) })
};
