import { api, ErrorApi } from '#lib/api';
import type { Load } from '@sveltejs/kit';

// La página del paciente se hidrata en el navegador: el enlace se comparte por WhatsApp y
// lo abre gente con cualquier conexión; no merece una espera de SSR por dos llamadas de lectura.
export const ssr = false;

export const load: Load = async ({ params }) => {
	const slug = params.slug ?? '';
	try {
		const ficha = await api.ficha(slug);
		return { slug, doctor: ficha.doctor, servicios: ficha.servicios, error: null };
	} catch (e) {
		if (e instanceof ErrorApi && e.status === 404) {
			return { slug, doctor: null, servicios: [], error: 404 };
		}
		throw e;
	}
};
