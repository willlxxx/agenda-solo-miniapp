import adapter from '@sveltejs/adapter-auto';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				runes: true
			}
		})
	],
	server: {
		// El backend va en :8000; en dev el proxy deja llamar a /api/... desde el navegador
		// sin configurar CORS ni puertos, y la cookie de sesión viaja same-origin.
		proxy: {
			'/api': 'http://localhost:8000'
		}
	}
});
