"""retención: cuántos meses guarda el consultorio una cita cerrada

El README lleva desde la Semana 1 prometiendo una retención de 6-12 meses para las citas pasadas, y
hasta ahora ese plazo vivía solo en el papel: no había columna donde guardarlo por consultorio ni
pasada que lo cumpliera.

Se añade con defecto 12 --el extremo conservador de la banda: borra lo menos posible hasta que el
médico diga lo contrario-- y con restricción para que un `INSERT` a mano no pueda guardar 0 (borraría
el registro contable en la siguiente pasada) ni 60 (no sería retención, sería acumular).

No necesita backfill: las citas existentes pasan a medirse contra el defecto, y la primera pasada de
limpieza sobre datos viejos es exactamente el comportamiento que se quiere ver.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("doctors", sa.Column("retencion_meses", sa.Integer(), server_default="12", nullable=False))
    op.create_check_constraint(
        "ck_doctor_retencion",
        "doctors",
        "retencion_meses >= 6 AND retencion_meses <= 12",
    )


def downgrade() -> None:
    op.drop_constraint("ck_doctor_retencion", "doctors", type_="check")
    op.drop_column("doctors", "retencion_meses")
