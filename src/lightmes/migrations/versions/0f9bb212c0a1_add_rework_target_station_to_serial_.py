"""add_rework_target_station_to_serial_units

Revision ID: 0f9bb212c0a1
Revises: a1b2c3d4e5f6
Create Date: 2026-08-10 11:58:00.923313
"""
from alembic import op
import sqlalchemy as sa


revision = '0f9bb212c0a1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "serial_units",
        sa.Column("rework_target_station_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_serial_units_rework_target_station_id_work_stations",
        "serial_units",
        "work_stations",
        ["rework_target_station_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_serial_units_rework_target_station_id_work_stations",
        "serial_units",
        type_="foreignkey",
    )
    op.drop_column("serial_units", "rework_target_station_id")
