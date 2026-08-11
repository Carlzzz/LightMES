"""add_planner_tables

Revision ID: b8d4f0a23c5e
Revises: a7c3e9f12b4d
Create Date: 2026-08-11 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d4f0a23c5e'
down_revision = 'a7c3e9f12b4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('shifts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('start_time', sa.String(), nullable=False),
        sa.Column('end_time', sa.String(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=True),
        sa.Column('line_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_shift_code'),
        sa.CheckConstraint(
            "start_time ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]$'",
            name='ck_shift_start_time_hhmm'),
        sa.CheckConstraint(
            "end_time ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]$'",
            name='ck_shift_end_time_hhmm'),
        sa.CheckConstraint(
            "json_typeof(days_of_week) = 'array' OR days_of_week IS NULL",
            name='ck_shift_days_of_week_array_or_null'),
        sa.ForeignKeyConstraint(['line_id'], ['lines.id']),
    )
    op.create_table('schedule_change_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('undone_from_log_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.CheckConstraint(
            "action IN ('schedule', 'unschedule', 'move', 'undo')",
            name='ck_schedule_change_log_action'),
    )
    op.create_index('ix_schedule_change_logs_work_order_id',
                    'schedule_change_logs', ['work_order_id'])
    op.add_column('work_orders',
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'))


def downgrade() -> None:
    op.drop_column('work_orders', 'priority')
    op.drop_index('ix_schedule_change_logs_work_order_id', table_name='schedule_change_logs')
    op.drop_table('schedule_change_logs')
    op.drop_table('shifts')
