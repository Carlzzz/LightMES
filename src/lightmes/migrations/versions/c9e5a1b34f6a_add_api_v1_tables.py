"""add_api_v1_tables

Revision ID: c9e5a1b34f6a
Revises: b8d4f0a23c5e
Create Date: 2026-08-12 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9e5a1b34f6a'
down_revision = 'b8d4f0a23c5e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('key_prefix', sa.String(length=16), nullable=False),
        sa.Column('key_hash', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_ip', sa.String(length=64), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['revoked_by'], ['users.id']),
    )
    op.create_index('ix_api_keys_key_prefix', 'api_keys', ['key_prefix'])
    op.create_index('ix_api_keys_user_id', 'api_keys', ['user_id'])
    op.create_table('api_call_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('trace_id', sa.String(length=32), nullable=True),
        sa.Column('client_ip', sa.String(length=64), nullable=True),
        sa.Column('error_detail', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )
    op.create_index('ix_api_call_logs_api_key_id', 'api_call_logs', ['api_key_id'])
    op.create_index('ix_api_call_logs_user_id', 'api_call_logs', ['user_id'])
    op.create_index('ix_api_call_logs_path', 'api_call_logs', ['path'])
    op.create_index('ix_api_call_logs_status_code', 'api_call_logs', ['status_code'])
    op.create_index('ix_api_call_logs_trace_id', 'api_call_logs', ['trace_id'])


def downgrade() -> None:
    op.drop_index('ix_api_call_logs_trace_id', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_status_code', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_path', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_user_id', table_name='api_call_logs')
    op.drop_index('ix_api_call_logs_api_key_id', table_name='api_call_logs')
    op.drop_table('api_call_logs')
    op.drop_index('ix_api_keys_user_id', table_name='api_keys')
    op.drop_index('ix_api_keys_key_prefix', table_name='api_keys')
    op.drop_table('api_keys')
