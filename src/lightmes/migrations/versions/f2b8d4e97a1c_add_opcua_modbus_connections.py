"""add_opcua_modbus_connections

Revision ID: f2b8d4e97a1c
Revises: e1a7c3d86b9f
Create Date: 2026-08-13 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b8d4e97a1c'
down_revision = 'e1a7c3d86b9f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('opcua_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('server_url', sa.String(length=500), nullable=False),
        sa.Column('security_mode', sa.String(length=20), nullable=False,
                  server_default='none'),
        sa.Column('username', sa.String(length=100), nullable=True),
        sa.Column('password_encrypted', sa.String(length=500), nullable=True),
        sa.Column('poll_interval_seconds', sa.Integer(), nullable=False,
                  server_default='5'),
        sa.Column('connect_timeout_seconds', sa.Integer(), nullable=False,
                  server_default='10'),
        sa.Column('reconnect_delay_seconds', sa.Integer(), nullable=False,
                  server_default='5'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_connection_id'],
                                ['machine_connections.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('machine_connection_id', name='uq_opcua_per_mc'),
    )
    op.create_index('ix_opcua_connections_machine_connection_id',
                    'opcua_connections', ['machine_connection_id'])

    op.create_table('modbus_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_connection_id', sa.Integer(), nullable=False),
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False, server_default='502'),
        sa.Column('slave_id', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('poll_interval_seconds', sa.Integer(), nullable=False,
                  server_default='5'),
        sa.Column('connect_timeout_seconds', sa.Integer(), nullable=False,
                  server_default='10'),
        sa.Column('reconnect_delay_seconds', sa.Integer(), nullable=False,
                  server_default='5'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['machine_connection_id'],
                                ['machine_connections.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('machine_connection_id', name='uq_modbus_per_mc'),
    )
    op.create_index('ix_modbus_connections_machine_connection_id',
                    'modbus_connections', ['machine_connection_id'])


def downgrade() -> None:
    op.drop_index('ix_modbus_connections_machine_connection_id',
                  table_name='modbus_connections')
    op.drop_table('modbus_connections')
    op.drop_index('ix_opcua_connections_machine_connection_id',
                  table_name='opcua_connections')
    op.drop_table('opcua_connections')
