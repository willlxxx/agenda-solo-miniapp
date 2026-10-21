"""recuperación de acceso: enlaces de un solo uso con caducidad

Faltaba la tabla y no el botón: hasta ahora un médico que olvidaba su contraseña no tenía por dónde
volver, porque lo único que existía era cambiarla con la sesión ya abierta (`POST /auth/contrasena`).

Se guarda el SHA-256 del token y no el token. Una base de datos de proveedor es un volcado legible por
quien tenga esas credenciales para otras cosas, y un hash irreversible de 32 bytes aleatorios no da
acceso a nada: con la tabla robada no se puede entrar en ninguna consulta.

No necesita backfill ni datos de origen: la tabla empieza vacía y solo crece cuando alguien pide
recuperar. Las filas gastadas (usadas o caducadas) las borra el propio servicio en la siguiente
solicitud del mismo consultorio, así que no depende de que un cron esté montado.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_envio", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE", name="fk_password_resets_doctor"),
        sa.PrimaryKeyConstraint("id", name="pk_password_resets"),
        sa.UniqueConstraint("token_hash", name="uq_password_resets_token"),
        sa.CheckConstraint("expira_en > creado_en", name="ck_password_reset_caducidad"),
    )
    # El cooldown y la limpieza de filas gastadas buscan por consultorio, en orden de llegada.
    op.create_index("ix_password_resets_doctor_creado", "password_resets", ["doctor_id", "creado_en"])


def downgrade() -> None:
    op.drop_index("ix_password_resets_doctor_creado", table_name="password_resets")
    op.drop_table("password_resets")
