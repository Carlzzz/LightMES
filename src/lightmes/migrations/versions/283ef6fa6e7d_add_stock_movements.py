"""add_stock_movements

Revision ID: 283ef6fa6e7d
Revises: 74eae97a39cb
Create Date: 2026-08-13 15:25:14.024535
"""
from alembic import op
import sqlalchemy as sa


revision = '283ef6fa6e7d'
down_revision = '74eae97a39cb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'stock_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('material_lot_id', sa.Integer(), nullable=False),
        sa.Column('movement_type', sa.String(length=20), nullable=False),
        sa.Column('quantity', sa.Numeric(12, 3), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=True),
        sa.Column('source_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "movement_type IN ('receive', 'release', 'consume', 'return', 'adjustment')",
            name='ck_stock_movements_type',
        ),
    )
    op.create_index(
        op.f('ix_stock_movements_material_lot_id'),
        'stock_movements',
        ['material_lot_id'],
        unique=False,
    )
    op.create_foreign_key(
        'fk_stock_movements_material_lot_id',
        'stock_movements',
        'material_lots',
        ['material_lot_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_stock_movements_material_lot_id',
        'stock_movements',
        type_='foreignkey',
    )
    op.drop_index(op.f('ix_stock_movements_material_lot_id'), table_name='stock_movements')
    op.drop_table('stock_movements')
