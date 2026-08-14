"""add_batch_material_lot_foundation

Revision ID: 1984dd11e9c1
Revises: 03bd1be9c599
Create Date: 2026-08-13 14:19:53.383150
"""
from alembic import op
import sqlalchemy as sa


revision = '1984dd11e9c1'
down_revision = '03bd1be9c599'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('batch_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('target_qty', sa.Integer(), nullable=False),
        sa.Column('produced_qty', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('work_order_id', 'batch_number', name='uq_batch_work_order_number'),
        sa.CheckConstraint("status IN ('pending', 'in_process', 'done', 'cancelled')", name='ck_batches_status'),
        sa.CheckConstraint('target_qty > 0', name='ck_batches_target_qty_positive'),
        sa.CheckConstraint('produced_qty >= 0', name='ck_batches_produced_qty_nonnegative'),
    )
    op.create_index(op.f('ix_batches_work_order_id'), 'batches', ['work_order_id'], unique=False)

    op.add_column('serial_units', sa.Column('batch_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_serial_units_batch_id', 'serial_units', 'batches', ['batch_id'], ['id']
    )
    op.create_index(op.f('ix_serial_units_batch_id'), 'serial_units', ['batch_id'], unique=False)

    op.create_table(
        'material_lots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Numeric(12, 3), nullable=False),
        sa.Column('available_quantity', sa.Numeric(12, 3), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('supplier_lot', sa.String(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_material_lots_code'),
        sa.CheckConstraint("status IN ('received', 'quarantined', 'released', 'consumed', 'rejected')", name='ck_material_lots_status'),
        sa.CheckConstraint('quantity >= 0', name='ck_material_lots_quantity_nonnegative'),
        sa.CheckConstraint('available_quantity >= 0', name='ck_material_lots_available_nonnegative'),
    )
    op.create_index(op.f('ix_material_lots_code'), 'material_lots', ['code'], unique=True)
    op.create_index(op.f('ix_material_lots_product_id'), 'material_lots', ['product_id'], unique=False)

    op.create_table(
        'batch_material_consumptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('material_lot_id', sa.Integer(), nullable=False),
        sa.Column('operation_record_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Numeric(12, 3), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('quantity >= 0', name='ck_batch_material_consumption_quantity_nonnegative'),
    )
    op.create_index(op.f('ix_batch_material_consumptions_batch_id'), 'batch_material_consumptions', ['batch_id'], unique=False)
    op.create_index(op.f('ix_batch_material_consumptions_material_lot_id'), 'batch_material_consumptions', ['material_lot_id'], unique=False)
    op.create_index(op.f('ix_batch_material_consumptions_operation_record_id'), 'batch_material_consumptions', ['operation_record_id'], unique=False)
    op.create_foreign_key('fk_bmc_batch_id', 'batch_material_consumptions', 'batches', ['batch_id'], ['id'])
    op.create_foreign_key('fk_bmc_material_lot_id', 'batch_material_consumptions', 'material_lots', ['material_lot_id'], ['id'])
    op.create_foreign_key('fk_bmc_operation_record_id', 'batch_material_consumptions', 'operation_records', ['operation_record_id'], ['id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_batch_material_consumptions_operation_record_id'), table_name='batch_material_consumptions')
    op.drop_index(op.f('ix_batch_material_consumptions_material_lot_id'), table_name='batch_material_consumptions')
    op.drop_index(op.f('ix_batch_material_consumptions_batch_id'), table_name='batch_material_consumptions')
    op.drop_constraint('fk_bmc_operation_record_id', 'batch_material_consumptions', type_='foreignkey')
    op.drop_constraint('fk_bmc_material_lot_id', 'batch_material_consumptions', type_='foreignkey')
    op.drop_constraint('fk_bmc_batch_id', 'batch_material_consumptions', type_='foreignkey')
    op.drop_table('batch_material_consumptions')

    op.drop_index(op.f('ix_material_lots_product_id'), table_name='material_lots')
    op.drop_index(op.f('ix_material_lots_code'), table_name='material_lots')
    op.drop_table('material_lots')

    op.drop_constraint('fk_serial_units_batch_id', 'serial_units', type_='foreignkey')
    op.drop_index(op.f('ix_serial_units_batch_id'), table_name='serial_units')
    op.drop_column('serial_units', 'batch_id')

    op.drop_index(op.f('ix_batches_work_order_id'), table_name='batches')
    op.drop_table('batches')
