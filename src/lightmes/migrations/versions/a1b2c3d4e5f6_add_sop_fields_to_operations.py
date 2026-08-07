"""add sop_text and sop_url to operations

Revision ID: a1b2c3d4e5f6
Revises: 0cda29e0e850
Create Date: 2026-08-07 21:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '0cda29e0e850'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('operations', sa.Column('sop_text', sa.Text(), nullable=True))
    op.add_column('operations', sa.Column('sop_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('operations', 'sop_url')
    op.drop_column('operations', 'sop_text')
