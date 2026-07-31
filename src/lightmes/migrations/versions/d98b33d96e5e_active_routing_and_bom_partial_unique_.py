"""active routing and bom partial unique indexes

Revision ID: d98b33d96e5e
Revises: e9c77d80efa8
Create Date: 2026-07-31 18:32:53.275615
"""
from alembic import op
import sqlalchemy as sa


revision = 'd98b33d96e5e'
down_revision = 'e9c77d80efa8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_active_routing_per_product", "routings", ["product_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_active_bom_per_product", "boms", ["product_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_bom_per_product", table_name="boms")
    op.drop_index("uq_active_routing_per_product", table_name="routings")
