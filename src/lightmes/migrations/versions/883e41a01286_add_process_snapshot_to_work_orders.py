"""add_process_snapshot_to_work_orders

Revision ID: 883e41a01286
Revises: 666eb38d12b1
Create Date: 2026-08-13 14:16:39.061660
"""
from alembic import op
import sqlalchemy as sa


revision = '883e41a01286'
down_revision = '666eb38d12b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('work_orders', sa.Column('process_snapshot', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('work_orders', 'process_snapshot')
