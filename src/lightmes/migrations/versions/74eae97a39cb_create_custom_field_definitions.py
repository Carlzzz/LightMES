"""create_custom_field_definitions

Revision ID: 74eae97a39cb
Revises: 1984dd11e9c1
Create Date: 2026-08-13 14:22:05.911598
"""
from alembic import op
import sqlalchemy as sa


revision = '74eae97a39cb'
down_revision = '1984dd11e9c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'custom_field_definitions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(length=100), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entity_type', 'key', name='uq_custom_field_entity_key'),
    )
    op.create_index(op.f('ix_custom_field_definitions_entity_type'), 'custom_field_definitions', ['entity_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_custom_field_definitions_entity_type'), table_name='custom_field_definitions')
    op.drop_table('custom_field_definitions')
