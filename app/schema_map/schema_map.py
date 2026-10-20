"""Versioned schema map: create -> review -> approve -> freeze -> (edit => new revision).

A frozen map carries a SHA-256 of its canonical content. Generation and migration
call `require_frozen()`, which recomputes the hash, so a tampered or edited map
is rejected and generated artifacts become invalid (see generation manifest).
"""
from __future__ import annotations
import copy, datetime as dt, hashlib, json

SCHEMA_MAP_FORMAT = 1

class SchemaMapError(Exception):
    pass

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def create_schema_map(schema: dict, similarity: dict, source_database: str,
                      review_threshold: float = 0.75, created_by: str = "schemashift") -> dict:
    scores = {(f["table"], f["column"]): f for f in similarity["fields"]}
    tables = copy.deepcopy(schema["tables"])
    for tname, t in tables.items():
        for cname, c in t["columns"].items():
            f = scores.get((tname, cname))
            if f is None:   # generated key
                c.update({"confidence": 1.0, "review_status": "auto_approved",
                          "review_reasons": [], "rationale": "generated surrogate key"})
                continue
            status = "needs_review" if f["needs_review"] else "auto_approved"
            c.update({"confidence": f["score"], "review_status": status,
                      "review_reasons": f["review_reasons"],
                      "rationale": "; ".join(f["notes"]) or "exact name/type/structure match"})
        for fk in t["foreign_keys"]:
            fk["review_status"] = "auto_approved" if fk["resolved"] else "needs_review"
    return {"format": SCHEMA_MAP_FORMAT, "revision": 1, "status": "draft",
            "source": {"database": source_database},
            "policies": {"review_threshold": review_threshold,
                         "nulls": "NULL allowed only when observed null/absent in source",
                         "unknown_fields": "never dropped; unmapped fields fail the migration"},
            "tables": tables,
            "approval": {"approved_by": None, "approved_at": None},
            "metadata": {"created_at": _now(), "created_by": created_by},
            "content_hash": None}

def canonical_hash(sm: dict) -> str:
    body = {"format": sm["format"], "source": sm["source"], "tables": sm["tables"],
            "policies": sm["policies"]}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()

def pending_reviews(sm: dict) -> list:
    out = []
    for tname, t in sm["tables"].items():
        for cname, c in t["columns"].items():
            if c["review_status"] == "needs_review":
                out.append({"table": tname, "column": cname, "reasons": c["review_reasons"]})
        for fk in t["foreign_keys"]:
            if fk["review_status"] == "needs_review":
                out.append({"table": tname, "column": fk["column"], "reasons": ["unresolved foreign key"]})
    return out

def _guard_editable(sm):
    if sm["status"] == "frozen":
        raise SchemaMapError("schema map is frozen; call start_new_revision() to edit it")

def apply_decision(sm: dict, table: str, column: str, action: str, *, sql_type=None,
                   new_name=None, ref_table=None, ref_column="id", by="reviewer") -> dict:
    """action: accept | reject | override. Overrides may change type, name or FK target."""
    _guard_editable(sm)
    try:
        t = sm["tables"][table]; c = t["columns"][column]
    except KeyError:
        raise SchemaMapError(f"unknown mapping {table}.{column}")
    if action == "accept":
        c["review_status"] = "approved"
    elif action == "reject":
        c["review_status"] = "rejected"
    elif action == "override":
        if sql_type:
            c["sql_type"] = sql_type
            c["mixed_types"] = False
        if ref_table:
            for fk in t["foreign_keys"]:
                if fk["column"] == column:
                    fk.update({"ref_table": ref_table, "ref_column": ref_column, "resolved": True,
                               "review_status": "approved"})
            if not any(fk["column"] == column for fk in t["foreign_keys"]):
                t["foreign_keys"].append({"column": column, "ref_table": ref_table,
                                          "ref_column": ref_column, "resolved": True,
                                          "review_status": "approved"})
            c["unresolved_fk"] = False
        if new_name and new_name != column:
            t["columns"][new_name] = t["columns"].pop(column)
            for fk in t["foreign_keys"]:
                if fk["column"] == column:
                    fk["column"] = new_name
            column = new_name
            c = t["columns"][column]
        c["review_status"] = "approved"
        c["rationale"] += f"; overridden by {by}"
    else:
        raise SchemaMapError(f"unknown action {action!r}")
    return sm

def freeze(sm: dict, approved_by: str) -> dict:
    _guard_editable(sm)
    pend = pending_reviews(sm)
    if pend:
        raise SchemaMapError(f"{len(pend)} mapping(s) still need review: "
                             + ", ".join(f"{p['table']}.{p['column']}" for p in pend[:5]))
    rejected = [(n, c) for n, t in sm["tables"].items() for c, v in t["columns"].items()
                if v["review_status"] == "rejected"]
    if rejected:
        raise SchemaMapError(f"rejected mappings must be resolved before freezing: {rejected[:5]}")
    sm["status"] = "frozen"
    sm["approval"] = {"approved_by": approved_by, "approved_at": _now()}
    sm["content_hash"] = canonical_hash(sm)
    return sm

def require_frozen(sm: dict) -> str:
    """Return the verified hash or raise. Downstream stages must call this first."""
    if sm.get("status") != "frozen" or not sm.get("content_hash"):
        raise SchemaMapError("schema map is not frozen/approved")
    if canonical_hash(sm) != sm["content_hash"]:
        raise SchemaMapError("frozen schema map was modified after freezing (hash mismatch)")
    return sm["content_hash"]

def start_new_revision(sm: dict) -> dict:
    new = copy.deepcopy(sm)
    new["revision"] = sm["revision"] + 1
    new["status"] = "draft"
    new["content_hash"] = None
    new["approval"] = {"approved_by": None, "approved_at": None}
    new["metadata"]["created_at"] = _now()
    return new
