"""recordatorios: cuándo salió cada aviso y cuántas veces lo intentamos

Las dos banderas booleanas que había desde la migración 0001 dicen *si* se avisó, pero no *cuándo*,
y con el cron externo eso es demasiado fino: si el proveedor falla a mitad de pasada necesitamos
saber cuántos intentos lleva la cita para no reintentar eternamente, y cuando el médico mire la
cita quiere ver la hora real del aviso, no un booleano.

Se añaden como opcionales porque las citas existentes ya tienen su bandera: `recordatorio_*_enviado`
sigue siendo la verdad del despacho (no se toca lo que ya funciona) y las columnas nuevas la
complementan. `recordatorio_intentos` no necesita backfill: 0 intentos es exactamente lo que
llevaron esas citas.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "appointments", sa.Column("recordatorio_24h_enviado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "appointments", sa.Column("recordatorio_1h_enviado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("appointments", sa.Column("recordatorio_intentos", sa.Integer(), server_default="0", nullable=False))
    # El cron pregunta por fechas sin saber el doctor: el índice existente (doctor_id, fecha_hora)
    # no sirve para ese barrido, y esta consulta corre en cada pasada.
    op.create_index(
        "ix_appointments_fecha_hora_confirmada",
        "appointments",
        ["fecha_hora"],
        unique=False,
        postgresql_where=sa.text("estado = 'confirmada'"),
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_fecha_hora_confirmada", table_name="appointments")
    op.drop_column("appointments", "recordatorio_intentos")
    op.drop_column("appointments", "recordatorio_1h_enviado_en")
    op.drop_column("appointments", "recordatorio_24h_enviado_en")
