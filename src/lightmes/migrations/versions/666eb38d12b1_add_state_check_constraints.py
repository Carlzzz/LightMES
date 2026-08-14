"""add_state_check_constraints

Revision ID: 666eb38d12b1
Revises: a1b9c2d3e4f5
Create Date: 2026-08-13 13:52:53.495855
"""
from alembic import op


revision = '666eb38d12b1'
down_revision = 'a1b9c2d3e4f5'
branch_labels = None
depends_on = None


_CHECKS = [
    ("sn_rules", "ck_sn_rules_seq_reset", "seq_reset IN ('never', 'daily', 'monthly')"),
    ("work_orders", "ck_work_orders_status", "status IN ('created', 'released', 'in_process', 'completed', 'cancelled')"),
    ("work_orders", "ck_work_orders_qty_positive", "qty > 0"),
    ("work_orders", "ck_work_orders_produced_qty_nonnegative", "produced_qty >= 0"),
    ("serial_units", "ck_serial_units_status", "status IN ('pending', 'in_process', 'reworking', 'quarantined', 'finished', 'scrapped')"),
    ("serial_units", "ck_serial_units_current_seq_nonnegative", "current_operation_seq >= 0"),
    ("serial_units", "ck_serial_units_version_nonnegative", "version >= 0"),
    ("operation_records", "ck_operation_records_result", "result IN ('pass', 'fail', 'skip')"),
    ("first_inspection_records", "ck_first_inspection_records_status", "status IN ('pending', 'passed', 'failed', 'waived')"),
    ("test_data_records", "ck_test_data_records_overall_result", "overall_result IN ('pending', 'passed', 'failed')"),
    ("defect_types", "ck_defect_types_severity", "severity IN ('critical', 'major', 'minor')"),
    ("defect_records", "ck_defect_records_handling_status", "handling_status IN ('pending', 'rework', 'scrap', 'concession')"),
]


def upgrade() -> None:
    for table, name, condition in _CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for table, name, _ in _CHECKS:
        op.drop_constraint(name, table, type_="check")
