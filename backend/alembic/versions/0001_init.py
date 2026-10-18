"""migración inicial: las 7 tablas del plan

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # citext: unicalidad sin mayúsculas/minúsculas para emails y slugs.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "doctors",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("telefono", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), server_default="Europe/Madrid", nullable=False),
        sa.Column("slug_unico", postgresql.CITEXT(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_doctors"),
        sa.UniqueConstraint("email", name="uq_doctors_email"),
        sa.UniqueConstraint("slug_unico", name="uq_doctors_slug"),
    )

    op.create_table(
        "patients",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("telefono", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("ultima_visita", sa.Date(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_patients_doctor"),
        sa.PrimaryKeyConstraint("id", name="pk_patients"),
        sa.UniqueConstraint("doctor_id", "nombre", "telefono", name="uq_patient_identidad"),
    )

    op.create_table(
        "services",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("duracion_minutos", sa.Integer(), nullable=False),
        sa.Column("color", sa.Text(), server_default="#2563eb", nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("orden", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_services_doctor"),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
        sa.CheckConstraint("duracion_minutos >= 5 AND duracion_minutos <= 240", name="ck_service_duracion"),
    )

    op.create_table(
        "availability",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("dia_semana", sa.SmallInteger(), nullable=False),
        sa.Column("hora_inicio", sa.Time(), nullable=False),
        sa.Column("hora_fin", sa.Time(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_availability_doctor"),
        sa.PrimaryKeyConstraint("id", name="pk_availability"),
        sa.UniqueConstraint("doctor_id", "dia_semana", "hora_inicio", name="uq_availability_inicio"),
        sa.CheckConstraint("dia_semana >= 0 AND dia_semana <= 6", name="ck_availability_dia"),
        sa.CheckConstraint("hora_fin > hora_inicio", name="ck_availability_horas"),
    )

    op.create_table(
        "blocked_dates",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_blocked_doctor"),
        sa.PrimaryKeyConstraint("id", name="pk_blocked_dates"),
        sa.UniqueConstraint("doctor_id", "fecha", name="uq_blocked_fecha"),
    )

    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("service_id", postgresql.UUID(), nullable=True),
        sa.Column("patient_id", postgresql.UUID(), nullable=True),
        sa.Column("fecha_hora", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duracion_minutos", sa.Integer(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="confirmada", nullable=False),
        sa.Column("consentimiento_ok_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recordatorio_24h_enviado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("recordatorio_1h_enviado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_appointments_doctor"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], name="fk_appointments_service"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], name="fk_appointments_patient"),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
        sa.CheckConstraint("estado IN ('confirmada','completada','no_vino','cancelada')", name="ck_appointment_estado"),
    )
    # Índice parcial: lo que hace rápido el motor de disponibilidad y el chequeo anti-dobles-reservas.
    op.execute(
        """
        CREATE INDEX ix_appointments_doctor_fecha_confirmada
        ON appointments (doctor_id, fecha_hora)
        WHERE estado = 'confirmada'
        """
    )

    op.create_table(
        "waitlist",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("service_id", postgresql.UUID(), nullable=True),
        sa.Column("patient_id", postgresql.UUID(), nullable=True),
        sa.Column("preferencia_horaria", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_waitlist_doctor"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], name="fk_waitlist_service"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], name="fk_waitlist_patient"),
        sa.PrimaryKeyConstraint("id", name="pk_waitlist"),
    )


def downgrade() -> None:
    op.drop_table("waitlist")
    op.drop_index("ix_appointments_doctor_fecha_confirmada", table_name="appointments")
    op.drop_table("appointments")
    op.drop_table("blocked_dates")
    op.drop_table("availability")
    op.drop_table("services")
    op.drop_table("patients")
    op.drop_table("doctors")
