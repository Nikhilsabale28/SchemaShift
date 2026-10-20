"""Deterministic document -> relational row transformation driven ONLY by a frozen schema map.

Rules (also documented in README):
  * ObjectId -> 24-char hex string (str()).      * naive datetimes are interpreted as UTC.
  * Decimal128/float -> NUMERIC only if no precision is lost, otherwise error.
  * missing/null in a NOT NULL column -> error;  any field not covered by the map -> error.
  * arrays of objects -> one child row per element, with parent key and `position` (array index).
Nothing is ever silently dropped or coerced lossy: problems raise TransformError.
"""
from __future__ import annotations
import datetime as dt
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

class TransformError(Exception):
    def __init__(self, collection, doc_id, path, message):
        self.collection, self.doc_id, self.path, self.message = collection, str(doc_id), path, message
        super().__init__(f"{collection}[{doc_id}] {path}: {message}")
    def as_dict(self):
        return {"collection": self.collection, "document_id": self.doc_id, "path": self.path,
                "message": self.message}

_NUM = re.compile(r"NUMERIC\((\d+),(\d+)\)")
_INT = re.compile(r"^-?\d+$")
_MISSING = object()

def get_path(obj, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur

def leaf_paths(value, path="") -> set:
    """All scalar/null leaf paths; arrays use `[]`. Empty containers have no leaves."""
    out = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out |= leaf_paths(v, f"{path}.{k}" if path else k)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= leaf_paths(v, f"{path}[]")
    else:
        out.add(path)
    return out

def to_decimal(v):
    if type(v).__name__ == "Decimal128":
        return v.to_decimal()
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise ValueError("boolean is not numeric")
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        return Decimal(v.strip())
    raise ValueError(f"{type(v).__name__} is not numeric")

def convert(value, sql_type: str):
    """Convert one non-null source value to the Python value stored in `sql_type`. Raises ValueError."""
    base = sql_type.split("(")[0].strip().upper()
    tn = type(value).__name__
    if base in ("TEXT", "VARCHAR"):
        if isinstance(value, str): out = value
        elif tn == "ObjectId" or tn == "FakeObjectId": out = str(value)
        elif isinstance(value, bool): out = "true" if value else "false"
        elif isinstance(value, (dt.datetime, dt.date)): out = value.isoformat()
        elif isinstance(value, (int, float, Decimal)) or tn == "Decimal128": out = str(value)
        else: raise ValueError(f"cannot store {tn} as text")
        m = re.match(r"VARCHAR\((\d+)\)", sql_type.upper())
        if m and len(out) > int(m.group(1)):
            raise ValueError(f"value longer than {m.group(1)} characters")
        return out
    if base in ("BIGINT", "INTEGER", "BIGSERIAL"):
        if isinstance(value, bool): raise ValueError("boolean cannot be stored as integer")
        if isinstance(value, int): return value
        if isinstance(value, float) and value.is_integer(): return int(value)
        if isinstance(value, str) and _INT.match(value.strip()): return int(value)
        raise ValueError(f"{tn} {value!r} is not a lossless integer")
    if base == "DOUBLE PRECISION":
        try: return float(to_decimal(value))
        except (ValueError, InvalidOperation) as e: raise ValueError(f"not a number: {value!r}") from e
    if base == "NUMERIC":
        m = _NUM.match(sql_type.upper())
        prec, scale = int(m.group(1)), int(m.group(2))
        try: d = to_decimal(value)
        except (ValueError, InvalidOperation) as e: raise ValueError(f"not a number: {value!r}") from e
        q = d.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN)
        if q != d: raise ValueError(f"{d} would lose precision in {sql_type}")
        if len(q.as_tuple().digits) > prec: raise ValueError(f"{d} exceeds {sql_type}")
        return q
    if base == "BOOLEAN":
        if isinstance(value, bool): return value
        raise ValueError(f"{tn} {value!r} is not a boolean")
    if base == "TIMESTAMPTZ":
        if isinstance(value, dt.datetime):
            return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
        if isinstance(value, dt.date):
            return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
        if isinstance(value, str):
            try:
                d = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as e: raise ValueError(f"unparseable date {value!r}") from e
            return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d
        raise ValueError(f"{tn} cannot be converted to a timestamp")
    raise ValueError(f"unsupported target type {sql_type}")

_NON_SOURCE = {"parent_key", "array_index", "generated"}

def tables_for(sm: dict, collection: str) -> dict:
    return {n: t for n, t in sm["tables"].items() if t["source_collection"] == collection}

def mapped_paths(sm: dict, collection: str) -> set:
    paths = {"_id"}
    for t in tables_for(sm, collection).values():
        for c in t["columns"].values():
            if c["source_path"]:
                paths.add(c["source_path"])
    return paths

def check_unmapped(sm, collection, doc, doc_id):
    mapped = mapped_paths(sm, collection)
    unmapped = []
    for p in sorted(leaf_paths(doc)):
        if p in mapped:
            continue
        if get_path(doc, p) is None and any(m.startswith(p + ".") or m.startswith(p + "[]") for m in mapped):
            continue  # null container (e.g. "shipping": null): its sub-columns become NULL
        unmapped.append(p)
    if unmapped:
        raise TransformError(collection, doc_id, ", ".join(unmapped),
                             "field(s) present in the document but not in the frozen schema map")

def _value_for(col, raw, collection, doc_id, path):
    if raw is _MISSING or raw is None:
        if col["nullable"]:
            return None
        raise TransformError(collection, doc_id, path, "required (NOT NULL) value is missing or null")
    try:
        return convert(raw, col["sql_type"])
    except ValueError as e:
        raise TransformError(collection, doc_id, path, str(e)) from e

def transform_document(sm: dict, collection: str, doc: dict) -> dict:
    """Return {table: [row dict]} for one document (parents and children)."""
    doc_id = doc.get("_id", "<no _id>")
    if "_id" not in doc:
        raise TransformError(collection, doc_id, "_id", "document has no _id")
    check_unmapped(sm, collection, doc, doc_id)
    out = {}
    for tname, spec in tables_for(sm, collection).items():
        cols = spec["columns"]
        if spec["kind"] == "root":
            row = {}
            for cname, col in cols.items():
                if col["source_path"] is None:
                    continue
                raw = get_path(doc, col["source_path"])
                row[cname] = _value_for(col, raw, collection, doc_id, col["source_path"])
            out[tname] = [row]
        else:
            arr = get_path(doc, spec["array_path"])
            rows = []
            if arr is not _MISSING and arr is not None:
                if not isinstance(arr, (list, tuple)):
                    raise TransformError(collection, doc_id, spec["array_path"], "expected an array")
                for idx, elem in enumerate(arr):
                    row = {}
                    for cname, col in cols.items():
                        tr = col.get("transform")
                        if tr == "generated":
                            continue
                        if tr == "parent_key":
                            row[cname] = str(doc["_id"]); continue
                        if tr == "array_index":
                            row[cname] = idx; continue
                        sp = col["source_path"]
                        prefix = spec["array_path"] + "[]"
                        if sp == prefix:
                            raw = elem
                        else:
                            if not isinstance(elem, dict):
                                raise TransformError(collection, doc_id, f"{prefix}", "array element is not an object")
                            raw = get_path(elem, sp[len(prefix) + 1:])
                        row[cname] = _value_for(col, raw, collection, doc_id, f"{spec['array_path']}[{idx}]"
                                                + ("" if sp == prefix else "." + sp[len(prefix) + 1:]))
                    rows.append(row)
            out[tname] = rows
    return out

def insert_columns(spec: dict) -> list:
    """Columns written by the loader (generated BIGSERIAL ids are left to the database)."""
    return sorted(c for c, v in spec["columns"].items() if v.get("transform") != "generated")

def conflict_columns(spec: dict) -> list:
    return list(spec["primary_key"]) if spec["kind"] == "root" else list(spec["unique"][0])
