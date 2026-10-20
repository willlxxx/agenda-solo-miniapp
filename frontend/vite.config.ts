import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			// El adaptador va DENTRO de sveltekit(): en SvelteKit 3 un plugin suelto de adapter se
			// ignora en silencio y el build termina con el aviso `adapter_missing`, que es justo lo que
			// daba por aquí antes.
			//
			// `fallback`: la interfaz es 100 % de navegador (`export const ssr = false` en el panel y en
			// la página del paciente), así que no hay HTML por ruta. Se sirve un único index.html y el
			// router de SvelteKit decide por la URL, lo que permite enlaces directos a /panel o a
			// /d/mi-consulta abiertos desde WhatsApp.
			adapter: adapter({ fallback: 'index.html' }),
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
