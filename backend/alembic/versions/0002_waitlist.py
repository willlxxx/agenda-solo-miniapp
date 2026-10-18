"""lista de espera: qué día quería el paciente y si ya le avisamos

Añade a `waitlist` el día pedido (nullable: hay quien acepta cualquier día), el estado de gestión
y el índice parcial con el que el panel lee solo lo pendiente. La unicalidad (doctor, paciente,
día) corta los duplicados de alguien que recarga la página.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("waitlist", sa.Column("fecha", sa.Date(), nullable=True))
    op.add_column("waitlist", sa.Column("estado", sa.Text(), server_default="en_espera", nullable=False))
    op.create_check_constraint("ck_waitlist_estado", "waitlist", "estado IN ('en_espera','contactada')")
    op.create_unique_constraint("uq_waitlist_paciente_dia", "waitlist", ["doctor_id", "patient_id", "fecha"])
    # Lo que el panel consulta todo el tiempo: lo pendiente de este doctor, en orden de llegada.
    op.execute(
        """
        CREATE INDEX ix_waitlist_doctor_estado
        ON waitlist (doctor_id, creado_en)
        WHERE estado = 'en_espera'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_waitlist_doctor_estado", table_name="waitlist")
    op.drop_constraint("uq_waitlist_paciente_dia", "waitlist", type_="unique")
    op.drop_constraint("ck_waitlist_estado", "waitlist", type_="check")
    op.drop_column("waitlist", "estado")
    op.drop_column("waitlist", "fecha")
