import { api, ErrorApi } from '#lib/api';
import type { Doctor } from '#lib/types';

/** Estado de sesión del doctor en el navegador. La fuente de verdad sigue siendo la cookie HttpOnly. */
class Sesion {
	doctor = $state<Doctor | null>(null);

	get autenticado(): boolean {
		return this.doctor !== null;
	}

	async cargar(): Promise<Doctor | null> {
		try {
			this.doctor = await api.yo();
		} catch (error) {
			// 401 esperado al no haber cookie; cualquier otro fallo también nos deja fuera.
			if (!(error instanceof ErrorApi && error.status === 401)) console.error(error);
			this.doctor = null;
		}
		return this.doctor;
	}

	async registrar(datos: {
		nombre: string;
		email: string;
		password: string;
		telefono?: string;
		timezone?: string;
	}): Promise<void> {
		this.doctor = await api.registro(datos);
	}

	async acceder(email: string, password: string): Promise<void> {
		this.doctor = await api.acceso(email, password);
	}

	async salir(): Promise<void> {
		await api.salida();
		this.doctor = null;
	}
}

export const sesion = new Sesion();
