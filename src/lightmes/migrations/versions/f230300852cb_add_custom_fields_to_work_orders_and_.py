"""add_custom_fields_to_work_orders_and_material_lots

Revision ID: f230300852cb
Revises: 283ef6fa6e7d
Create Date: 2026-08-14 09:56:38.564547
"""
from alembic import op
import sqlalchemy as sa


revision = 'f230300852cb'
down_revision = '283ef6fa6e7d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('work_orders', sa.Column('custom_fields', sa.JSON(), nullable=True))
    op.add_column('material_lots', sa.Column('custom_fields', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('material_lots', 'custom_fields')
    op.drop_column('work_orders', 'custom_fields')
