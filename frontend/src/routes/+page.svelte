<script lang="ts">
	import { goto } from '$app/navigation';
	import { sesion } from '#lib/sesion.svelte';

	$effect(() => {
		if (sesion.doctor) goto('/panel');
	});
</script>

<main class="contenedor">
	<h1>Tu agenda llena sin interrupciones</h1>
	<p>
		Tus pacientes reservan solos, reciben recordatorios automáticos, y tú solo ves quién viene y
		cuándo. Pensado para el odontólogo que trabaja solo.
	</p>

	<div class="tarjeta">
		<h2>Empieza en 2 minutos</h2>
		<p class="aviso">
			Sin instalación y sin curva de aprendizaje: ya viene con tus servicios y tu horario
			preconfigurados, solo los ajustas si quieres.
		</p>
		<div class="fila">
			<button class="boton" onclick={() => goto('/auth/registro')}>Crear mi cuenta</button>
			<button class="boton--suave" onclick={() => goto('/auth/acceso')}>Ya tengo cuenta</button>
		</div>
	</div>

	<p class="aviso">$29/mes · un solo plan · cancela cuando quieras</p>
</main>
