<script lang="ts">
	import { goto } from '$app/navigation';
	import Icon from '#lib/Icon.svelte';
	import { sesion } from '#lib/sesion.svelte';

	$effect(() => {
		if (sesion.doctor) goto('/panel');
	});
</script>

<main class="contenedor">
	<!-- La marca abre la página: si un paciente reconoce el diente en el enlace, el médico también. -->
	<span class="marca-diente"><Icon name="diente" tamaño={44} /></span>

	<h1>Tu agenda llena sin interrupciones</h1>
	<p>
		Tus pacientes reservan solos, reciben recordatorios automáticos, y tú solo ves quién viene y
		cuándo. Pensado para el odontólogo que trabaja solo.
	</p>

	<div class="tarjeta">
		<h2><Icon name="reloj" tamaño={18} />Empieza en 2 minutos</h2>
		<p class="aviso">
			Sin instalación y sin curva de aprendizaje: ya viene con tus servicios y tu horario
			preconfigurados, solo los ajustas si quieres.
		</p>
		<div class="fila">
			<button class="boton" onclick={() => goto('/auth/registro')}>
				<Icon name="mas" tamaño={16} />Crear mi cuenta
			</button>
			<button class="boton--suave" onclick={() => goto('/auth/acceso')}>
				<Icon name="derecha" tamaño={16} />Ya tengo cuenta
			</button>
		</div>
	</div>

	<p class="aviso">$29/mes · un solo plan · cancela cuando quieras</p>
</main>

<style>
	/* El héroe va centrado para que la marca y el nombre de la app formen una sola pieza. */
	main {
		display: flex;
		flex-direction: column;
		align-items: center;
		text-align: center;
		padding-top: 2rem;
	}

	main > * {
		width: 100%;
		max-width: 34rem;
	}

	.marca-diente {
		display: inline-flex;
		width: auto;
		padding: 0.6rem;
		margin-bottom: 1rem;
		border-radius: 50%;
		color: var(--primario);
		background: var(--primario-suave);
	}

	.tarjeta {
		text-align: left;
	}
</style>
