"""MQTT message parser — format parsing, JSONPath field resolution, condition evaluation.

Adapted from OpenMES MqttMessageParser with Python idioms.
"""
import csv
import io
import json
import re


class MqttMessageParser:
    """Parse raw MQTT payloads and resolve field paths / conditions."""

    def parse(self, payload: str, fmt: str) -> dict:
        """Parse payload by declared format. Returns dict on success, {'_raw': ..., '_error': ...} on failure."""
        try:
            if fmt == "json":
                decoded = json.loads(payload)
                return decoded if isinstance(decoded, dict) else {"value": decoded}
            elif fmt == "plain":
                return {"value": payload}
            elif fmt == "csv":
                reader = csv.reader(io.StringIO(payload.strip()))
                rows = [row for row in reader if row]
                return {"rows": rows}
            elif fmt == "hex":
                cleaned = payload.strip().replace(" ", "")
                byte_vals = [int(cleaned[i:i+2], 16) for i in range(0, len(cleaned), 2)]
                return {"hex": cleaned, "bytes": byte_vals}
            else:
                return {"_raw": payload, "_error": f"Unknown format: {fmt}"}
        except Exception as e:
            return {"_raw": payload, "_error": str(e)}

    def resolve_path(self, path: str | None, data: dict) -> any:
        """Resolve a JSONPath-like path from parsed data.

        "$.field" → data["field"]
        "$.nested.field" → data["nested"]["field"]
        "$.arr.0" → data["arr"][0]
        No "$" prefix → literal value
        None → entire data dict
        """
        if path is None:
            return data
        if not path.startswith("$"):
            return path
        if path == "$":
            return data
        keys = path[2:].split(".")  # strip "$."
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list):
                try:
                    idx = int(key)
                    value = value[idx]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return value

    def evaluate_condition(self, expr: str | None, resolved_value) -> bool:
        """Evaluate a simple condition expression against a resolved value.

        Supported: value == X, value != X, value > X, value >= X, value < X, value <= X, value contains X
        None → True (always pass). Unparseable → True (fail-safe).
        """
        if expr is None or not expr.strip():
            return True
        # Match "<lhs> <op> <literal>" — lhs is any identifier (value, status, code, ...)
        m = re.match(r"^[A-Za-z_]\w*\s*(==|!=|>=|<=|>|<|contains)\s*(.+)$", expr.strip())
        if not m:
            return True  # fail-safe
        op, literal = m.group(1), m.group(2).strip()
        # Coerce literal
        if literal.lower() == "true":
            lit = True
        elif literal.lower() == "false":
            lit = False
        elif literal.lower() == "null":
            lit = None
        else:
            try:
                lit = int(literal)
            except ValueError:
                try:
                    lit = float(literal)
                except ValueError:
                    lit = literal  # string
        try:
            if op == "==":
                return resolved_value == lit
            elif op == "!=":
                return resolved_value != lit
            elif op == ">":
                return resolved_value > lit
            elif op == ">=":
                return resolved_value >= lit
            elif op == "<":
                return resolved_value < lit
            elif op == "<=":
                return resolved_value <= lit
            elif op == "contains":
                return str(lit) in str(resolved_value)
        except TypeError:
            return False  # type mismatch
        return True
