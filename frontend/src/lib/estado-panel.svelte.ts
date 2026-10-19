/** Cuántas veces se escribió algo desde el panel, para que las vistas no se queden viejas.

La pantalla del médico tiene Agenda, Avisos y Lista de espera leyendo la misma base a la vez. Cuando
Avisos guarda una cita por él, las otras dos siguen mostrando lo de antes: la petición aparece abajo
como pendiente y la cita no aparece arriba, y eso se lee como un fallo. Basta con un contador: no nos
interesa qué cambió, solo que cambió.
*/
class EstadoPanel {
	version = $state(0);

	avisar(): void {
		this.version += 1;
	}
}

export const panel = new EstadoPanel();
