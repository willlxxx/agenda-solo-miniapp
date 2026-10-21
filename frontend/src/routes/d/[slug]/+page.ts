import { api, ErrorApi } from '#lib/api';
import type { Load } from '@sveltejs/kit';

// La página del paciente se hidrata en el navegador: el enlace se comparte por WhatsApp y
// lo abre gente con cualquier conexión; no merece una espera de SSR por dos llamadas de lectura.
export const ssr = false;

export const load: Load = async ({ params }) => {
	const slug = params.slug ?? '';
	try {
		const ficha = await api.ficha(slug);
		// El canal viaja con la ficha porque es lo que sostiene (o no) la promesa del recordatorio.
		return {
			slug,
			doctor: ficha.doctor,
			servicios: ficha.servicios,
			avisos_de_email: ficha.avisos_de_email,
			canal_de_avisos: ficha.canal_de_avisos,
			error: null
		};
	} catch (e) {
		if (e instanceof ErrorApi && e.status === 404) {
			return {
				slug,
				doctor: null,
				servicios: [],
				// Sin ficha no hay formulario: nada que prometer y ningún canal que declarar.
				avisos_de_email: false,
				canal_de_avisos: 'desconocido',
				error: 404
			};
		}
		throw e;
	}
};
