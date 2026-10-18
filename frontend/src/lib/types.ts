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
