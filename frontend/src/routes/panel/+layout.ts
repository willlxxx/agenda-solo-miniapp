import { redirect } from '@sveltejs/kit';
import { api, ErrorApi } from '#lib/api';
import { sesion } from '#lib/sesion.svelte';

// El panel es una app, no contenido: no necesita SEO, y la cookie viaja desde el navegador.
export const ssr = false;

export const load = async () => {
	const doctor = await api
		.yo()
		.catch((error) => {
			if (error instanceof ErrorApi && error.status === 401) return null;
			throw error;
		});

	if (!doctor) {
		sesion.doctor = null;
		throw redirect(303, '/auth/acceso');
	}

	sesion.doctor = doctor;
	return { doctor };
};
