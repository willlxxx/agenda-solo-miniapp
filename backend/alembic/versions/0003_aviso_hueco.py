"""aviso de hueco: qué le ofrecimos a quien estaba en lista de espera, y de dónde salió cada cita

`waitlist` guarda el hueco que se ofreció y el momento del aviso, para que el panel no insista
dos veces con el mismo día y para poder decir "ya le avisé de esto". `appointments.origen`
distingue la reserva autoclictada de la que hizo el médico por teléfono: la palabra "confirmada"
significa lo mismo, pero el consentimiento no.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("waitlist", sa.Column("avisado_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("waitlist", sa.Column("hueco_ofrecido", sa.DateTime(timezone=True), nullable=True))
    # Las citas que ya existían se reservaron todas desde el enlace público.
    op.add_column("appointments", sa.Column("origen", sa.Text(), server_default="publico", nullable=False))
    op.create_check_constraint("ck_appointment_origen", "appointments", "origen IN ('publico','panel')")


def downgrade() -> None:
    op.drop_constraint("ck_appointment_origen", "appointments", type_="check")
    op.drop_column("appointments", "origen")
    op.drop_column("waitlist", "hueco_ofrecido")
    op.drop_column("waitlist", "avisado_en")
