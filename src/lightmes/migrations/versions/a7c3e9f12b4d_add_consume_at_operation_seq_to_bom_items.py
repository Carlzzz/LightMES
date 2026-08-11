"""add_consume_at_operation_seq_to_bom_items

Revision ID: a7c3e9f12b4d
Revises: 49ca97c7b192
Create Date: 2026-08-11 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c3e9f12b4d'
down_revision = '49ca97c7b192'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bom_items",
        sa.Column("consume_at_operation_seq", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bom_items", "consume_at_operation_seq")
