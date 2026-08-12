"""add_connectivity_tables

Revision ID: d0f6b2c75a8e
Revises: c9e5a1b34f6a
Create Date: 2026-08-13 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd0f6b2c75a8e'
down_revision = 'c9e5a1b34f6a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('machine_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('protocol', sa.String(length=20), nullable=False, server_default='mqtt'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='disconnected'),
        sa.Column('status_message', sa.String(length=500), nullable=True),
        sa.Column('last_connected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('messages_received', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_machine_connection_name'),
    )
    op.create_table('mqtt_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('broker_host', sa.String(length=255), nullable=False),
        sa.Column('broker_port', sa.Integer(), nullable=False, server_default='1883'),
        sa.Column('client_id', sa.String(length=100), nullable=True),
        sa.Column('username', sa.String(length=100), nullable=True),
        sa.Column('password_encrypted', sa.String(length=500), nullable=True),
        sa.Column('use_tls', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('keep_alive_seconds', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('qos_default', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clean_session', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('connect_timeout_seconds', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('reconnect_delay_seconds', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('machine_connection_id', name='uq_mqtt_per_machine_connection'),
        sa.ForeignKeyConstraint(['machine_connection_id'], ['machine_connections.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_mqtt_connections_machine_connection_id',
                    'mqtt_connections', ['machine_connection_id'])
    op.create_table('machine_topics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('topic_pattern', sa.String(length=500), nullable=False),
        sa.Column('payload_format', sa.String(length=20), nullable=False, server_default='json'),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_connection_id'], ['machine_connections.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_machine_topics_machine_connection_id',
                    'machine_topics', ['machine_connection_id'])
    op.create_table('machine_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=500), nullable=False),
        sa.Column('raw_payload', sa.Text(), nullable=False),
        sa.Column('matched_topic_id', sa.Integer(), nullable=True),
        sa.Column('processing_status', sa.String(length=20), nullable=False, server_default='ok'),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_connection_id'], ['machine_connections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matched_topic_id'], ['machine_topics.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_machine_messages_machine_connection_id',
                    'machine_messages', ['machine_connection_id'])
    op.create_index('ix_machine_messages_conn_received',
                    'machine_messages', ['machine_connection_id', 'received_at'])
    op.create_index('ix_machine_messages_received',
                    'machine_messages', ['received_at'])


def downgrade() -> None:
    op.drop_index('ix_machine_messages_received', table_name='machine_messages')
    op.drop_index('ix_machine_messages_conn_received', table_name='machine_messages')
    op.drop_index('ix_machine_messages_machine_connection_id', table_name='machine_messages')
    op.drop_table('machine_messages')
    op.drop_index('ix_machine_topics_machine_connection_id', table_name='machine_topics')
    op.drop_table('machine_topics')
    op.drop_index('ix_mqtt_connections_machine_connection_id', table_name='mqtt_connections')
    op.drop_table('mqtt_connections')
    op.drop_table('machine_connections')
