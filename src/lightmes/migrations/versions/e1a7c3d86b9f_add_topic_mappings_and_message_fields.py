"""add_topic_mappings_and_message_fields

Revision ID: e1a7c3d86b9f
Revises: d0f6b2c75a8e
Create Date: 2026-08-13 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e1a7c3d86b9f'
down_revision = 'd0f6b2c75a8e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('topic_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_topic_id', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('field_path', sa.String(length=255), nullable=True),
        sa.Column('action_type', sa.String(length=30), nullable=False),
        sa.Column('action_params', sa.JSON(), nullable=True),
        sa.Column('condition_expr', sa.String(length=255), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_topic_id'], ['machine_topics.id'], ondelete='CASCADE'),
        sa.CheckConstraint(
            "action_type IN ('log_event', 'update_work_order_produced_qty', "
            "'set_work_order_status', 'update_serial_unit_status', "
            "'create_defect', 'webhook_forward')",
            name='ck_topic_mappings_action_type'),
    )
    op.create_index('ix_topic_mappings_machine_topic_id',
                    'topic_mappings', ['machine_topic_id'])
    op.add_column('machine_messages', sa.Column('parsed_data', sa.JSON(), nullable=True))
    op.add_column('machine_messages', sa.Column('actions_triggered', sa.JSON(), nullable=True))
    op.add_column('machine_messages', sa.Column('processing_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('machine_messages', 'processing_error')
    op.drop_column('machine_messages', 'actions_triggered')
    op.drop_column('machine_messages', 'parsed_data')
    op.drop_index('ix_topic_mappings_machine_topic_id', table_name='topic_mappings')
    op.drop_table('topic_mappings')
