"""add issue andon

Revision ID: a1b9c2d3e4f5
Revises: f2b8d4e97a1c
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b9c2d3e4f5"
down_revision = "f2b8d4e97a1c"
branch_labels = None
depends_on = None


SEED_TYPES = [
    {"code": "material_shortage", "name": "缺料", "severity": "major", "is_blocking": True, "is_active": True, "description": "缺料异常"},
    {"code": "quality", "name": "质量异常", "severity": "major", "is_blocking": False, "is_active": True, "description": "质量异常"},
    {"code": "tool_failure", "name": "工装失效", "severity": "major", "is_blocking": True, "is_active": True, "description": "工装/夹具失效"},
    {"code": "equipment_fault", "name": "设备故障", "severity": "critical", "is_blocking": True, "is_active": True, "description": "设备故障"},
    {"code": "safety", "name": "安全问题", "severity": "critical", "is_blocking": True, "is_active": True, "description": "EHS 相关"},
    {"code": "other", "name": "其他", "severity": "minor", "is_blocking": False, "is_active": True, "description": "其他"},
]


def upgrade():
    op.create_table(
        "issue_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_issue_types_code"),
        sa.CheckConstraint("severity IN ('info', 'minor', 'major', 'critical')", name="ck_issue_types_severity"),
    )

    op.create_table(
        "issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_type_id", sa.Integer(), sa.ForeignKey("issue_types.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("serial_unit_id", sa.Integer(), sa.ForeignKey("serial_units.id"), nullable=True),
        sa.Column("work_order_id", sa.Integer(), sa.ForeignKey("work_orders.id"), nullable=True),
        sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=True),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("defect_id", sa.Integer(), sa.ForeignKey("defect_records.id"), nullable=True),
        sa.Column("reported_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("acknowledged_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(15), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("containment_action", sa.Text(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved', 'closed')", name="ck_issues_status"),
        sa.CheckConstraint("severity IN ('info', 'minor', 'major', 'critical')", name="ck_issues_severity"),
        sa.CheckConstraint("source IN ('station_andon', 'defect_linked', 'manual')", name="ck_issues_source"),
        sa.CheckConstraint("disposition IS NULL OR disposition IN ('use_as_is', 'rework', 'scrap', 'hold')", name="ck_issues_disposition"),
    )
    op.create_index("ix_issues_status", "issues", ["status"])
    op.create_index("ix_issues_serial_unit_id", "issues", ["serial_unit_id"])
    op.create_index("ix_issues_work_order_id", "issues", ["work_order_id"])
    op.create_index("ix_issues_work_station_id", "issues", ["work_station_id"])

    op.create_table(
        "issue_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.Integer(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(15), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assigned_to_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("type IN ('corrective', 'preventive', 'containment')", name="ck_issue_actions_type"),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'done', 'verified')", name="ck_issue_actions_status"),
    )
    op.create_index("ix_issue_actions_issue_id", "issue_actions", ["issue_id"])

    # Seed 默认类型
    issue_types = sa.table(
        "issue_types",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("severity", sa.String),
        sa.column("is_blocking", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(issue_types, SEED_TYPES)


def downgrade():
    op.drop_table("issue_actions")
    op.drop_table("issues")
    op.drop_table("issue_types")
