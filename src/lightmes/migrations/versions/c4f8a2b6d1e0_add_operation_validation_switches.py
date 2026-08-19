"""add operation validation switches

Revision ID: c4f8a2b6d1e0
Revises: b2e1f7a9c3d5
Create Date: 2026-08-18 14:00:00.000000

工序级校验开关：require_material_binding（过站强制校验物料绑定完整性）、
require_param_collection（过站强制校验工艺参数已采集）。默认关闭，保持既有行为。
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4f8a2b6d1e0'
down_revision = 'b2e1f7a9c3d5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operations",
        sa.Column("require_material_binding", sa.Boolean(),
                  nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "operations",
        sa.Column("require_param_collection", sa.Boolean(),
                  nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("operations", "require_param_collection")
    op.drop_column("operations", "require_material_binding")
