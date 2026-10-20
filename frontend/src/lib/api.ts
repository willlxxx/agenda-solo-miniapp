import type {
	AgendaVentana,
	AvisosHueco,
	Cita,
	CitaAgenda,
	DiaBloqueado,
	Disponibilidad,
	Doctor,
	EntradaLista,
	EstadoCita,
	FichaPaciente,
	FichaPublica,
	Franja,
	ListaDeEsperaPublica,
	ListaEspera,
	ReservaAsistida,
	Retencion,
	Servicio,
	Supresion
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
		request<CitaAgenda>(`/appointments/${id}`, { method: 'PATCH', body: JSON.stringify({ estado }) }),

	// Lista de espera: el paciente se apunta por su enlace; el doctor gestiona desde el panel.
	apuntarse: (slug: string, datos: object) =>
		request<ListaDeEsperaPublica>(`/public/${encodeURIComponent(slug)}/lista-espera`, json(datos)),
	listaEspera: (filtro: 'pendientes' | 'contactadas' | 'todas' = 'pendientes') =>
		request<ListaEspera>(`/waitlist?filtro=${filtro}`),
	marcarLista: (id: string, estado: 'en_espera' | 'contactada') =>
		request<EntradaLista>(`/waitlist/${id}`, { method: 'PATCH', body: JSON.stringify({ estado }) }),
	quitarLista: (id: string) => request<void>(`/waitlist/${id}`, { method: 'DELETE' }),

	// Avisos de hueco: es lectura (cruza la lista con la disponibilidad real) y el envío lo hace él.
	encajes: (dias = 21) => request<AvisosHueco>(`/waitlist/encajes?dias=${dias}`),
	// El hueco se revalida en el servidor con el mismo motor: si ya no está libre, devuelve 409.
	reservarPorEl: (id: string, datos: { fecha: string; hora: string }) =>
		request<ReservaAsistida>(`/waitlist/${id}/reservar`, json(datos)),

	// Ficha del paciente: la abre el médico desde una cita. El email y el histórico no viajan en la
	// lista de la agenda, así que solo se piden cuando hay una pantalla delante que los justifica.
	fichaDeCita: (citaId: string) => request<FichaPaciente>(`/pacientes/de-cita/${citaId}`),
	suprimirPaciente: (id: string) => request<Supresion>(`/pacientes/${id}`, { method: 'DELETE' }),
	/**
	 * Enlace de la descarga, no una llamada: el navegador guarda el archivo con el nombre que decide
	 * el servidor (`Content-Disposition`) sin pasar por JSON ni por un botón propio.
	 */
	url_de_datos: (id: string) => `${BASE}/pacientes/${id}/datos`,

	// Retención: el médico elige el plazo, pero quién borra es el cron. Cambiar el número no elimina
	// nada en el momento, así que esta pantalla puede ser tranquila.
	retencion: () => request<Retencion>('/retencion'),
	guardarRetencion: (meses: number) =>
		request<Retencion>('/retencion', { method: 'PUT', body: JSON.stringify({ meses }) })
};
