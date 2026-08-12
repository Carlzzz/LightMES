import pytest
from lightmes.modules.connectivity.parser import MqttMessageParser


@pytest.fixture
def p():
    return MqttMessageParser()


# --- parse ---

def test_parse_json(p):
    result = p.parse('{"count": 5, "status": "ok"}', "json")
    assert result["count"] == 5
    assert result["status"] == "ok"


def test_parse_plain(p):
    result = p.parse("hello world", "plain")
    assert result == {"value": "hello world"}


def test_parse_csv(p):
    result = p.parse("a,b,c\n1,2,3", "csv")
    assert result["rows"] == [["a", "b", "c"], ["1", "2", "3"]]


def test_parse_hex(p):
    result = p.parse("48656c6c6f", "hex")
    assert result["hex"] == "48656c6c6f"
    assert result["bytes"] == [0x48, 0x65, 0x6c, 0x6c, 0x6f]


def test_parse_invalid_json(p):
    result = p.parse("{bad json", "json")
    assert "_raw" in result
    assert "_error" in result


def test_parse_unknown_format(p):
    result = p.parse("data", "xml")
    assert "_raw" in result


# --- resolve_path ---

def test_resolve_path_nested(p):
    data = {"a": {"b": {"c": 42}}}
    assert p.resolve_path("$.a.b.c", data) == 42


def test_resolve_path_array(p):
    data = {"arr": [10, 20, 30]}
    assert p.resolve_path("$.arr.0", data) == 10
    assert p.resolve_path("$.arr.2", data) == 30


def test_resolve_path_literal(p):
    assert p.resolve_path("literal_value", {"a": 1}) == "literal_value"


def test_resolve_path_none(p):
    data = {"x": 1}
    assert p.resolve_path(None, data) == data


def test_resolve_path_missing(p):
    data = {"a": 1}
    assert p.resolve_path("$.b", data) is None


# --- evaluate_condition ---

def test_condition_gt(p):
    assert p.evaluate_condition("value > 5", 10) is True
    assert p.evaluate_condition("value > 5", 3) is False


def test_condition_eq(p):
    assert p.evaluate_condition("status == active", "active") is True
    assert p.evaluate_condition("status == active", "inactive") is False


def test_condition_contains(p):
    assert p.evaluate_condition("code contains ERR", "ERROR_001") is True
    assert p.evaluate_condition("code contains ERR", "OK") is False


def test_condition_none_always_true(p):
    assert p.evaluate_condition(None, "anything") is True


def test_condition_unparseable(p):
    assert p.evaluate_condition("garbage expression", 1) is True
