export type Doctor = {
	id: string;
	nombre: string;
	email: string;
	telefono: string | null;
	timezone: string;
	slug_unico: string;
};

/** Lo que `PATCH /auth/yo` acepta. El `id` no se edita y el email/ enlace los valida el backend. */
export type PerfilEdicion = {
	nombre?: string;
	email?: string;
	telefono?: string | null;
	timezone?: string;
	slug_unico?: string;
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
	/** Cuándo le avisamos; null si sigue sin avisar. */
	avisado_utc: string | null;
	/** El hueco que le ofrecimos, para no insistir sobre la misma petición. */
	hueco_ofrecido_utc: string | null;
};

export type ListaEspera = {
	total: number;
	en_espera: number;
	contactadas: number;
	filtro: 'pendientes' | 'contactadas' | 'todas';
	entradas: EntradaLista[];
};

/**
 * Una petición pendiente con el hueco real que ya le sirve, y el aviso escrito con sus enlaces.
 * El backend no envía nada: `whatsapp` y `correo` son wa.me y mailto: que el médico abre desde su
 * propia cuenta, así que no hay ningún tercero mandando mensajes por él.
 */
export type AvisoHueco = {
	id: string;
	nombre: string;
	telefono: string | null;
	email: string | null;
	servicio: string | null;
	/** Lo que pidió el paciente: por eso este hueco es suyo y no otro. */
	pidio_fecha: string | null;
	preferencia: PreferenciaLista | null;
	fecha: string;
	inicio: string;
	fin: string;
	mensaje: string;
	whatsapp: string | null;
	correo: string | null;
};

export type AvisosHueco = {
	timezone: string;
	desde: string;
	hasta: string;
	pendientes: number;
	con_hueco: number;
	avisos: AvisoHueco[];
};

/** Cita que el médico guarda por el paciente desde su panel. */
export type ReservaAsistida = {
	id: string;
	fecha: string;
	hora: string;
	fin: string;
	duracion_minutos: number;
	servicio: string;
	doctor: string;
	paciente: string;
	/** La fila de la lista, ya cerrada como `contactada`. */
	entrada: EntradaLista;
};

/** Una cita tal y como la ve el dueño de la agenda, dentro de la ficha de su paciente. */
export type CitaDeFicha = {
	id: string;
	inicio_utc: string;
	fecha: string;
	hora: string;
	fin: string;
	duracion_minutos: number;
	estado: EstadoCita;
	/** `publico`: la clicó el paciente; `panel`: la escribió el médico por él. */
	origen: 'publico' | 'panel';
	servicio: string | null;
	consentimiento_en: string;
	recordatorio_24h_en: string | null;
	recordatorio_1h_en: string | null;
	recordatorio_intentos: number;
};

export type PeticionDeFicha = {
	id: string;
	fecha: string | null;
	preferencia: PreferenciaLista | null;
	estado: string;
	servicio: string | null;
	pedido_utc: string;
	avisado_utc: string | null;
};

/**
 * Lo que hay guardado sobre una persona. Sale solo al abrir su ficha desde una cita —la lista de la
 * agenda pinta nombre y teléfono, nada más— y es la base del export y del borrado (RGPD).
 */
export type FichaPaciente = {
	id: string;
	nombre: string;
	telefono: string | null;
	email: string | null;
	ultima_visita: string | null;
	creado_en: string;
	timezone: string;
	citas: CitaDeFicha[];
	peticiones: PeticionDeFicha[];
	/** Lo que el médico va a perder si borra: texto del servidor, con las cuentas de esta ficha. */
	aviso: string;
};

export type Supresion = {
	paciente: string;
	citas_sin_identidad: number;
	citas_por_llegar: number;
	peticiones_sin_identidad: number;
	aviso: string;
};

/**
 * El plazo que el médico elige (6-12 meses) y la frase del servidor que explica qué hace. La frase
 * viene del backend porque es la regla de borrado: si el panel la escribiera, quedaría obsoleta en
 * cuanto cambiemos el criterio y seguiría prometiéndose en pantalla.
 */
export type Retencion = {
	meses: number;
	explicacion: string;
};

/**
 * Lo que contesta `GET /auth/recuperar/disponible`. `razon` llega solo cuando el despliegue no puede
 * mandar el enlace (`sin_canal_de_correo`) o cuando puede pero por un canal que no es correo real en
 * desarrollo (`simulado_en_desarrollo`): la pantalla lo necesita para decir la verdad en vez de
 * prometer un buzón que no existe.
 */
export type DisponibilidadRecuperacion = {
	disponible: boolean;
	razon: string | null;
};

/** `POST /auth/recuperar/solicitar`: 202 y esta única forma, exista la cuenta o no. */
export type SolicitudRecuperacion = {
	aceptada: boolean;
};
