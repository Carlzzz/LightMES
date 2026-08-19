"""add scrap_qty to work_orders; source/duration_seconds to downtimes

Revision ID: b2e1f7a9c3d5
Revises: 73fe375a0d78
Create Date: 2026-08-18 10:00:00.000000

报废闭环：工单增加 scrap_qty 累计字段（历史按 scrapped SN 回填）。
停机归因：production_downtimes 增加 source（machine/manual，历史全为 machine）
与 duration_seconds（秒级精度，与 OEE 口径一致，按已有 duration_minutes 回填）。
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2e1f7a9c3d5'
down_revision = '73fe375a0d78'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("scrap_qty", sa.Integer(), nullable=False, server_default="0"),
    )
    # 回填：已报废 SN 数量计入对应工单
    op.execute("""
        UPDATE work_orders wo
        SET scrap_qty = COALESCE(su.cnt, 0)
        FROM (
            SELECT work_order_id, COUNT(*) AS cnt
            FROM serial_units
            WHERE status = 'scrapped' AND work_order_id IS NOT NULL
            GROUP BY work_order_id
        ) su
        WHERE su.work_order_id = wo.id
    """)

    op.add_column(
        "production_downtimes",
        sa.Column("source", sa.String(20), nullable=False, server_default="machine"),
    )
    op.add_column(
        "production_downtimes",
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )
    # 回填秒级时长（有 ended_at 的记录按精确时间差，open 记录置 NULL 待关闭时写）
    op.execute("""
        UPDATE production_downtimes
        SET duration_seconds = GREATEST(
            0, EXTRACT(EPOCH FROM (ended_at - started_at))::INTEGER)
        WHERE ended_at IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("production_downtimes", "duration_seconds")
    op.drop_column("production_downtimes", "source")
    op.drop_column("work_orders", "scrap_qty")
