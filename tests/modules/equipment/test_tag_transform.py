import pytest

from lightmes.modules.equipment.models import MachineTag
from lightmes.modules.equipment.tag_service import TagService


def _tag(transform=None):
    return MachineTag(machine_topic_id=1, name="t", field_path="$.s",
                      signal_type="state", transform=transform)


def test_value_map_matches_key(db_session):
    tag = _tag({"value_map": {"1": "RUNNING", "2": "IDLE"}})
    assert TagService(db_session).apply_transform(tag, 1) == "RUNNING"
    assert TagService(db_session).apply_transform(tag, "2") == "IDLE"


def test_value_map_default(db_session):
    tag = _tag({"value_map": {"1": "RUNNING", "default": "UNKNOWN"}})
    assert TagService(db_session).apply_transform(tag, 99) == "UNKNOWN"


def test_value_map_no_match_no_default(db_session):
    tag = _tag({"value_map": {"1": "RUNNING"}})
    assert TagService(db_session).apply_transform(tag, 99) == 99


def test_scale_offset(db_session):
    tag = _tag({"scale": 0.1, "offset": -50})
    assert TagService(db_session).apply_transform(tag, 1000) == 50.0


def test_numeric_string_coerced(db_session):
    tag = _tag({"scale": 2})
    assert TagService(db_session).apply_transform(tag, "25") == 50.0


def test_non_numeric_passthrough(db_session):
    tag = _tag()
    assert TagService(db_session).apply_transform(tag, "hello") == "hello"
