"""Deterministic post-migration verification. PASS only if EVERY check passes; computed by code only.

What is compared exactly: for every target row, all loader columns (canonicalized) against the row
independently re-derived from the source documents by the same frozen-map transform.
What cannot be compared byte-for-byte (documented limitation):
  * generated child `id` (BIGSERIAL) - rows are matched by their natural key (parent id + array position);
  * ObjectId -> text and naive-datetime -> UTC conversions are compared in their converted form;
  * nested documents split into several tables are verified per table, not as one reassembled document.
"""
from __future__ import annotations
import datetime as dt, hashlib, json
from decimal import Decimal, InvalidOperation
from ..etl.transform import transform_document, TransformError, insert_columns, conflict_columns, convert
from ..generation.ddl import dependency_order
from ..schema_map.schema_map import require_frozen, SchemaMapError

DEFAULT_CRITICAL = ("email", "price", "unit_price", "quantity", "status", "order_date", "customer_id", "product_id")

def _now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def canon(value, sql_type: str):
    """Canonical JSON-safe form of a stored value; raises ValueError if the type is wrong."""
    if value is None:
        return None
    base = sql_type.split("(")[0].upper()
    if base == "TIMESTAMPTZ":
        if not isinstance(value, dt.datetime): raise ValueError(f"expected datetime, got {type(value).__name__}")
        v = value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)
        return v.isoformat(timespec="microseconds")
    if base == "NUMERIC":
        try:
            d = value if isinstance(value, Decimal) else Decimal(str(value))
            return format(convert(d, sql_type), "f")
        except InvalidOperation as e:
            raise ValueError(f"not a valid number: {value!r}") from e
    if base == "DOUBLE PRECISION":
        try: return repr(float(value))
        except (TypeError, ValueError) as e: raise ValueError(f"not a valid float: {value!r}") from e
    if base in ("BIGINT", "INTEGER", "BIGSERIAL"):
        if isinstance(value, bool) or not isinstance(value, int): raise ValueError(f"expected int, got {type(value).__name__}")
        return value
    if base == "BOOLEAN":
        if not isinstance(value, bool): raise ValueError(f"expected bool, got {type(value).__name__}")
        return value
    if not isinstance(value, str): raise ValueError(f"expected text, got {type(value).__name__}")
    return value

def _canon_row(spec, row):
    return {c: canon(row.get(c), spec["columns"][c]["sql_type"]) for c in insert_columns(spec)}

def _nk(spec, canon_row): return tuple(canon_row[c] for c in conflict_columns(spec))
def _checksum(rows) -> str:
    blob = json.dumps(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()

def verify_migration(sm: dict, source, sink, critical_fields=DEFAULT_CRITICAL, max_examples: int = 10) -> dict:
    rep = {"verified_at": _now(), "status": "FAIL", "map_hash": None, "checks": [], "mismatches": [],
           "limitations": __doc__.split("What cannot be compared")[1].strip().splitlines()}
    def check(name, ok, detail):
        rep["checks"].append({"name": name, "passed": bool(ok), "detail": detail})
        return ok
    try:
        rep["map_hash"] = require_frozen(sm)
    except SchemaMapError as e:
        check("frozen_map", False, str(e)); return rep
    expected = {t: [] for t in sm["tables"]}
    errors = []
    for coll in sorted({t["source_collection"] for t in sm["tables"].values()}):
        for doc in source.iter_documents(coll):
            try:
                for t, rs in transform_document(sm, coll, doc).items():
                    expected[t].extend(rs)
            except TransformError as e:
                errors.append(e.as_dict())
    check("source_transformable", not errors, f"{len(errors)} source document(s) fail transformation" if errors else "all source documents transform cleanly")
    actual_canon, expected_canon = {}, {}
    for t in dependency_order(sm["tables"]):
        spec = sm["tables"][t]
        try:
            actual = sink.fetch_rows(t, spec)
        except Exception as e:
            check(f"{t}:readable", False, f"{type(e).__name__}: {e}"); continue
        n_exp, n_act = len(expected[t]), len(actual)
        check(f"{t}:count", n_exp == n_act, f"source-derived {n_exp} vs target {n_act}")
        try:
            a = [_canon_row(spec, r) for r in actual]
            check(f"{t}:types", True, "all stored values have the expected types")
        except ValueError as e:
            check(f"{t}:types", False, str(e)); a = []
        e_rows = [_canon_row(spec, r) for r in expected[t]]
        actual_canon[t], expected_canon[t] = a, e_rows
        nulls = [(c, r) for r in a for c, v in spec["columns"].items()
                 if v.get("transform") != "generated" and not v["nullable"] and r.get(c) is None]
        check(f"{t}:required_fields", not nulls, f"{len(nulls)} NULLs in NOT NULL columns")
        amap, emap = {_nk(spec, r): r for r in a}, {_nk(spec, r): r for r in e_rows}
        missing, extra = sorted(set(emap) - set(amap), key=str), sorted(set(amap) - set(emap), key=str)
        diffs = []
        for k in sorted(set(emap) & set(amap), key=str):
            for c in emap[k]:
                if emap[k][c] != amap[k][c]:
                    diffs.append({"table": t, "key": list(k), "column": c, "expected": emap[k][c],
                                  "actual": amap[k][c], "critical": c in critical_fields})
        for k in missing[:max_examples]: rep["mismatches"].append({"table": t, "key": list(k), "problem": "missing in target"})
        for k in extra[:max_examples]: rep["mismatches"].append({"table": t, "key": list(k), "problem": "unexpected extra row in target"})
        rep["mismatches"].extend(diffs[:max_examples])
        check(f"{t}:rows_present", not missing and not extra, f"{len(missing)} missing, {len(extra)} extra")
        crit = [d for d in diffs if d["critical"]]
        check(f"{t}:critical_fields", not crit, f"{len(crit)} critical value mismatches ({', '.join(sorted({d['column'] for d in crit})) or 'none'})")
        check(f"{t}:checksum", _checksum(a) == _checksum(e_rows) and not diffs,
              f"sha256 target={_checksum(a)[:12]} expected={_checksum(e_rows)[:12]}, {len(diffs)} differing values")
    for t, spec in sm["tables"].items():
        for fk in spec["foreign_keys"]:
            refs = {r.get(fk["ref_column"]) for r in actual_canon.get(fk["ref_table"], [])}
            if fk["ref_column"] == "id" and sm["tables"][fk["ref_table"]]["kind"] != "root":
                continue
            orphans = [r[fk["column"]] for r in actual_canon.get(t, []) if r.get(fk["column"]) is not None and r[fk["column"]] not in refs]
            check(f"{t}.{fk['column']}->{fk['ref_table']}:foreign_key", not orphans, f"{len(orphans)} orphan references")
    rep["status"] = "PASS" if rep["checks"] and all(c["passed"] for c in rep["checks"]) else "FAIL"
    rep["summary"] = {"checks": len(rep["checks"]), "failed": sum(not c["passed"] for c in rep["checks"])}
    if rep["status"] == "FAIL":
        rep["recovery"] = ("Do not use the target data. Inspect mismatches[]; fix the cause, then either re-run the "
                           "migration (idempotent) after correcting/clearing the demo schema via the rollback script "
                           "(requires ALLOW_DESTRUCTIVE_CLEANUP=true), or change the mapping, re-approve and re-freeze.")
    return rep
