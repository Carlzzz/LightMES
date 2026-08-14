"""create_equipment_tables

Revision ID: 73fe375a0d78
Revises: f230300852cb
Create Date: 2026-08-14 15:07:26.762944
"""
from alembic import op
import sqlalchemy as sa


revision = '73fe375a0d78'
down_revision = 'f230300852cb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("machine_topic_id", sa.Integer(), sa.ForeignKey("machine_topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("field_path", sa.String(255), nullable=False),
        sa.Column("signal_type", sa.String(20), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=True),
        sa.Column("transform", sa.JSON(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("last_count_value", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("machine_topic_id", "field_path", "signal_type", name="uq_machine_tag_topic_field_signal"),
        sa.CheckConstraint("signal_type IN ('state','good_count','reject_count','cycle_complete','telemetry','alarm')", name="ck_machine_tags_signal_type"),
    )
    op.create_index("ix_machine_tags_machine_topic_id", "machine_tags", ["machine_topic_id"])

    op.create_table(
        "workstation_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default=sa.text("'machine'")),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("state IN ('RUNNING','IDLE','STOPPED','FAULT','SETUP','WAITING','CLEANING','MAINTENANCE')", name="ck_workstation_states_state"),
        sa.CheckConstraint("source IN ('machine','manual')", name="ck_workstation_states_source"),
    )
    op.create_index("ix_workstation_states_work_station_id", "workstation_states", ["work_station_id"])
    op.create_index("ix_ws_state_station_started", "workstation_states", ["work_station_id", "started_at"])
    op.create_index("ix_ws_state_station_ended", "workstation_states", ["work_station_id", "ended_at"])

    op.create_table(
        "downtime_reasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_downtime_reason_code"),
        sa.CheckConstraint("kind IN ('planned','unplanned')", name="ck_downtime_reason_kind"),
    )

    op.create_table(
        "production_downtimes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("lines.id"), nullable=False),
        sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=False),
        sa.Column("downtime_reason_id", sa.Integer(), sa.ForeignKey("downtime_reasons.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_planned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_production_downtimes_work_station_id", "production_downtimes", ["work_station_id"])
    op.create_index("ix_production_downtimes_line_id", "production_downtimes", ["line_id"])
    op.create_index("ix_downtime_station", "production_downtimes", ["work_station_id"])
    op.create_index("ix_downtime_line", "production_downtimes", ["line_id"])

    op.add_column("machine_connections", sa.Column("work_station_id", sa.Integer(), sa.ForeignKey("work_stations.id"), nullable=True))
    op.create_index("ix_machine_connections_work_station_id", "machine_connections", ["work_station_id"])


def downgrade() -> None:
    op.drop_index("ix_machine_connections_work_station_id", table_name="machine_connections")
    op.drop_column("machine_connections", "work_station_id")
    op.drop_index("ix_downtime_line", table_name="production_downtimes")
    op.drop_index("ix_downtime_station", table_name="production_downtimes")
    op.drop_index("ix_production_downtimes_line_id", table_name="production_downtimes")
    op.drop_index("ix_production_downtimes_work_station_id", table_name="production_downtimes")
    op.drop_table("production_downtimes")
    op.drop_table("downtime_reasons")
    op.drop_index("ix_ws_state_station_ended", table_name="workstation_states")
    op.drop_index("ix_ws_state_station_started", table_name="workstation_states")
    op.drop_index("ix_workstation_states_work_station_id", table_name="workstation_states")
    op.drop_table("workstation_states")
    op.drop_index("ix_machine_tags_machine_topic_id", table_name="machine_tags")
    op.drop_table("machine_tags")
