"""Type detection and SQL type compatibility rules (deterministic)."""
from __future__ import annotations
import datetime as _dt
import decimal

OBJECT_ID = "objectid"

def type_name(value) -> str:
    """Return a canonical source type name for a Python/BSON value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, decimal.Decimal):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "date"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if type(value).__name__ in ("ObjectId", "FakeObjectId"):
        return OBJECT_ID
    if type(value).__name__ == "Decimal128":
        return "decimal"
    return "unknown"

SQL_TYPE = {
    "string": "TEXT", "integer": "BIGINT", "float": "DOUBLE PRECISION",
    "decimal": "NUMERIC(12,2)", "boolean": "BOOLEAN", "date": "TIMESTAMPTZ",
    OBJECT_ID: "VARCHAR(24)", "unknown": "TEXT",
}

_FAMILY = {
    "TEXT": "text", "VARCHAR": "text", "BIGINT": "int", "INTEGER": "int",
    "DOUBLE PRECISION": "float", "NUMERIC": "num", "BOOLEAN": "bool",
    "TIMESTAMPTZ": "ts", "TIMESTAMP": "ts", "JSONB": "json",
}
_SRC_FAMILY = {"string": "text", "integer": "int", "float": "float", "decimal": "num",
               "boolean": "bool", "date": "ts", OBJECT_ID: "text", "unknown": "text",
               "object": "json", "array": "json", "null": "text"}
_COMPAT = {
    ("int", "int"): 1.0, ("text", "text"): 1.0, ("bool", "bool"): 1.0, ("ts", "ts"): 1.0,
    ("float", "float"): 1.0, ("num", "num"): 1.0, ("json", "json"): 1.0,
    ("int", "num"): 0.9, ("int", "float"): 0.8, ("float", "num"): 0.9, ("num", "float"): 0.8,
    ("float", "int"): 0.3, ("num", "int"): 0.3,
    ("text", "ts"): 0.2, ("ts", "text"): 0.6, ("int", "text"): 0.5, ("float", "text"): 0.5,
    ("num", "text"): 0.5, ("bool", "text"): 0.5, ("text", "json"): 0.4,
    ("json", "text"): 0.3,
}

def sql_family(sql_type: str) -> str:
    base = sql_type.split("(")[0].strip().upper()
    return _FAMILY.get(base, "text")

def type_compatibility(source_type: str, sql_type: str) -> float:
    """1.0 = lossless, 0.0 = incompatible. Unlisted pairs are incompatible."""
    return _COMPAT.get((_SRC_FAMILY.get(source_type, "text"), sql_family(sql_type)), 0.0)

def dominant_sql_type(type_counts: dict[str, int]) -> tuple[str, bool]:
    """Pick a SQL type from observed type frequencies.
    Returns (sql_type, mixed); mixed=True when non-null types disagree
    in a way that is not a safe numeric widening."""
    kinds = {k: v for k, v in type_counts.items() if k != "null"}
    if not kinds:
        return "TEXT", False
    if len(kinds) == 1:
        return SQL_TYPE[next(iter(kinds))], False
    if set(kinds) <= {"integer", "float", "decimal"}:
        return ("NUMERIC(12,2)" if "decimal" in kinds else "DOUBLE PRECISION"), False
    return "TEXT", True
