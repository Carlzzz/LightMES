"""serial_units.work_order_id nullable (component inventory SNs)

Revision ID: d9c4e7f1a2b8
Revises: c4f8a2b6d1e0
Create Date: 2026-08-18 14:30:00.000000

唯一件组件的序列号档案（入库组件，无工单）需要 serial_units 行；
料号校验（扫组件 SN → 解析 product）依赖该档案存在。
"""
from alembic import op
import sqlalchemy as sa


revision = 'd9c4e7f1a2b8'
down_revision = 'c4f8a2b6d1e0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "serial_units", "work_order_id",
        existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute(
        "UPDATE serial_units SET work_order_id = 0 WHERE work_order_id IS NULL")
    op.alter_column(
        "serial_units", "work_order_id",
        existing_type=sa.Integer(), nullable=False)
