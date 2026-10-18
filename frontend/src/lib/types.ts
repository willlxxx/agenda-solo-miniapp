export type Doctor = {
	id: string;
	nombre: string;
	email: string;
	telefono: string | null;
	timezone: string;
	slug_unico: string;
};

export type Servicio = {
	id: string;
	nombre: string;
	duracion_minutos: number;
	color: string;
	activo: boolean;
	orden: number;
};

/** 0 = lunes ... 6 = domingo */
export type Franja = {
	id: string;
	dia_semana: number;
	hora_inicio: string;
	hora_fin: string;
	activo: boolean;
};

export type DiaBloqueado = {
	id: string;
	fecha: string;
	motivo: string | null;
};

/** Enlace público de reservas: lo que ve el paciente, sin datos del doctor que no hacen falta */
export type DoctorResumen = {
	nombre: string;
	slug_unico: string;
	timezone: string;
};

export type FichaPublica = {
	doctor: DoctorResumen;
	servicios: Servicio[];
};

export type Hueco = {
	inicio: string;
	fin: string;
};

export type DiaDisponible = {
	fecha: string;
	huecos: Hueco[];
};

export type Disponibilidad = {
	doctor: DoctorResumen;
	servicio: Servicio;
	paso_minutos: number;
	anticipacion_minutos: number;
	/** Horizonte consultado: los días sin huecos no salen en `dias`, así que hay que saberlo. */
	desde: string;
	hasta: string;
	dias: DiaDisponible[];
};

export type Cita = {
	id: string;
	fecha: string;
	hora: string;
	fin: string;
	duracion_minutos: number;
	servicio: string;
	doctor: string;
};

/** Estado de cita: 'confirmada' es la única que bloquea el hueco en la disponibilidad. */
export type EstadoCita = 'confirmada' | 'completada' | 'no_vino' | 'cancelada';

export type PacienteAgenda = {
	nombre: string;
	telefono: string | null;
	email: string | null;
};

/** Cita vista por el doctor: UTC para calcular, hora local del doctor para leer. */
export type CitaAgenda = {
	id: string;
	inicio_utc: string;
	fecha: string;
	hora: string;
	fin: string;
	duracion_minutos: number;
	estado: EstadoCita;
	servicio: string | null;
	color: string;
	paciente: PacienteAgenda | null;
};

export type AgendaVentana = {
	timezone: string;
	desde: string;
	hasta: string;
	total: number;
	confirmadas: number;
	citas: CitaAgenda[];
};

/** Qué hora del día prefiere el paciente de la lista. Viaja como literal, se pinta en español. */
export type PreferenciaLista = 'manana' | 'tarde' | 'cualquier_hora';

export const PREFERENCIAS: { valor: PreferenciaLista; texto: string }[] = [
	{ valor: 'cualquier_hora', texto: 'Cualquier hora' },
	{ valor: 'manana', texto: 'Por la mañana' },
	{ valor: 'tarde', texto: 'Por la tarde' }
];

/** Respuesta del POST público: `creado` false significa "ya estabas apuntado". */
export type ListaDeEsperaPublica = {
	id: string;
	doctor: string;
	servicio: string;
	fecha: string | null;
	preferencia: PreferenciaLista;
	estado: string;
	creado: boolean;
};

export type EntradaLista = {
	id: string;
	nombre: string;
	telefono: string | null;
	email: string | null;
	servicio: string | null;
	fecha: string | null;
	preferencia: PreferenciaLista | null;
	estado: 'en_espera' | 'contactada';
	pedido_utc: string;
};

export type ListaEspera = {
	total: number;
	en_espera: number;
	contactadas: number;
	filtro: 'pendientes' | 'contactadas' | 'todas';
	entradas: EntradaLista[];
};
