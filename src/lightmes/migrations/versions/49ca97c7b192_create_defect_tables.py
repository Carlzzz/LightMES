"""create_defect_tables

Revision ID: 49ca97c7b192
Revises: 2afb2fd4624a
Create Date: 2026-08-10 16:31:40.522013
"""
from alembic import op
import sqlalchemy as sa


revision = '49ca97c7b192'
down_revision = '2afb2fd4624a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('defect_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_defect_types_code'), 'defect_types', ['code'], unique=True)
    op.create_table('defect_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('defect_type_id', sa.Integer(), nullable=False),
        sa.Column('defect_type_code', sa.String(), nullable=False),
        sa.Column('defect_type_name', sa.String(), nullable=False),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('serial_unit_id', sa.Integer(), nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=True),
        sa.Column('work_station_id', sa.Integer(), nullable=True),
        sa.Column('position', sa.String(), nullable=True),
        sa.Column('discovered_by', sa.Integer(), nullable=False),
        sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('handling_status', sa.String(), nullable=False),
        sa.Column('handled_by', sa.Integer(), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('handling_remark', sa.String(), nullable=True),
        sa.Column('remark', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['defect_type_id'], ['defect_types.id'], ),
        sa.ForeignKeyConstraint(['discovered_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['handled_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], ),
        sa.ForeignKeyConstraint(['serial_unit_id'], ['serial_units.id'], ),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id'], ),
        sa.ForeignKeyConstraint(['work_station_id'], ['work_stations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_defect_records_defect_type_id'), 'defect_records', ['defect_type_id'], unique=False)
    op.create_index(op.f('ix_defect_records_serial_unit_id'), 'defect_records', ['serial_unit_id'], unique=False)
    op.create_index(op.f('ix_defect_records_work_order_id'), 'defect_records', ['work_order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_defect_records_work_order_id'), table_name='defect_records')
    op.drop_index(op.f('ix_defect_records_serial_unit_id'), table_name='defect_records')
    op.drop_index(op.f('ix_defect_records_defect_type_id'), table_name='defect_records')
    op.drop_table('defect_records')
    op.drop_index(op.f('ix_defect_types_code'), table_name='defect_types')
    op.drop_table('defect_types')
