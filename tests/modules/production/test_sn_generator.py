from datetime import datetime
import pytest
from lightmes.modules.production.sn_generator import (
    validate_pattern, period_key, render, SnGenerator,
)
from lightmes.modules.production.models import SnRule


def test_validate_pattern_accepts_valid():
    validate_pattern("SN{YY}{MM}{DD}{SEQ:5}")  # no raise


def test_validate_pattern_rejects_unknown_placeholder():
    with pytest.raises(ValueError):
        validate_pattern("SN{FOO}{SEQ:3}")


def test_validate_pattern_rejects_seq_without_width():
    with pytest.raises(ValueError):
        validate_pattern("SN{SEQ}")


def test_period_key():
    now = datetime(2026, 7, 31, 10, 0, 0)
    assert period_key("never", now) == "*"
    assert period_key("daily", now) == "2026-07-31"
    assert period_key("monthly", now) == "2026-07"


def test_render_pads_seq_and_date():
    now = datetime(2026, 7, 5, 0, 0, 0)
    assert render("SN{YY}{MM}{DD}{SEQ:4}", 42, now) == "SN2607050042"


def test_next_sn_increments(db_session):
    rule = SnRule(code="R", name="r", pattern="A{SEQ:3}", seq_reset="never")
    db_session.add(rule)
    db_session.flush()
    gen = SnGenerator(db_session)
    now = datetime(2026, 7, 31)
    assert gen.next_sn(rule, now) == "A001"
    assert gen.next_sn(rule, now) == "A002"


def test_next_sn_resets_on_new_period(db_session):
    rule = SnRule(code="R2", name="r", pattern="{SEQ:2}", seq_reset="daily")
    db_session.add(rule)
    db_session.flush()
    gen = SnGenerator(db_session)
    assert gen.next_sn(rule, datetime(2026, 7, 31)) == "01"
    assert gen.next_sn(rule, datetime(2026, 7, 31)) == "02"
    assert gen.next_sn(rule, datetime(2026, 8, 1)) == "01"  # reset new day


def test_validate_pattern_rejects_seq_non_digit_width():
    with pytest.raises(ValueError):
        validate_pattern("SN{SEQ:abc}")


def test_validate_pattern_rejects_seq_empty_width():
    with pytest.raises(ValueError):
        validate_pattern("SN{SEQ:}")


def test_validate_pattern_rejects_missing_seq():
    with pytest.raises(ValueError):
        validate_pattern("SN{YYYY}")


def test_validate_pattern_rejects_pure_literal():
    with pytest.raises(ValueError):
        validate_pattern("FIXED")


def test_next_sn_refreshes_from_locked_row(db_session):
    from lightmes.modules.production.models import SnRule

    rule = SnRule(code="RL", name="r", pattern="{SEQ:2}", seq_reset="never")
    db_session.add(rule)
    db_session.flush()
    gen = SnGenerator(db_session)
    assert gen.next_sn(rule, datetime(2026, 7, 31)) == "01"
    # 人为把内存实例改成过期值；populate_existing 应从锁定行刷新覆盖它
    rule.current_seq = 999
    assert gen.next_sn(rule, datetime(2026, 7, 31)) == "02"
