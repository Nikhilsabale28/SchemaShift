"""Migration runner: validates, transforms EVERYTHING first, then loads in one transaction.

Policy (documented in README):
  * Frozen + hash-verified map and current generated artifacts are mandatory.
  * Phase 1 transforms all documents; if any TransformError occurs nothing is written (status FAILED_TRANSFORM).
  * Phase 2 loads tables parents-first in batches inside ONE transaction; any error rolls everything back.
  * Re-runs are idempotent: rows whose key already exists are skipped (ON CONFLICT DO NOTHING) and reported;
    verification then compares VALUES, so a stale/different existing row is still detected.
"""
from __future__ import annotations
import datetime as dt
from ..schema_map.schema_map import require_frozen, SchemaMapError
from ..generation.ddl import dependency_order
from ..etl.transform import transform_document, TransformError

def _now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def run_migration(sm: dict, source, sink, ddl_sql: str, *, batch_size: int = 500, progress=print) -> dict:
    report = {"started_at": _now(), "status": "RUNNING", "map_hash": None, "map_revision": sm.get("revision"),
              "collections": {}, "tables": {}, "errors": [], "message": ""}
    def fail(status, msg):
        report.update({"status": status, "message": msg, "finished_at": _now()})
        return report
    try:
        report["map_hash"] = require_frozen(sm)
    except SchemaMapError as e:
        return fail("FAILED_PRECONDITION", str(e))
    try:
        progress("checking source and target connectivity ...")
        source.ping(); sink.ping()
        wanted = sorted({t["source_collection"] for t in sm["tables"].values()})
        available = set(source.collection_names())
        missing = [c for c in wanted if c not in available]
        if missing:
            return fail("FAILED_PRECONDITION", f"source collections missing: {missing}")
        sink.apply_schema(sm, ddl_sql)
    except Exception as e:
        return fail("FAILED_PRECONDITION", f"{type(e).__name__}: {e}")

    rows = {t: [] for t in sm["tables"]}
    for coll in wanted:
        n = 0
        for doc in source.iter_documents(coll):
            n += 1
            try:
                for t, rs in transform_document(sm, coll, doc).items():
                    rows[t].extend(rs)
            except TransformError as e:
                report["errors"].append(e.as_dict())
        report["collections"][coll] = {"documents_read": n}
        progress(f"  transformed {coll}: {n} documents")
    if report["errors"]:
        return fail("FAILED_TRANSFORM", f"{len(report['errors'])} document(s) could not be transformed; "
                    "nothing was written. See errors[] for document ids and paths.")
    try:
        with sink.transaction():
            for t in dependency_order(sm["tables"]):
                spec = sm["tables"][t]
                inserted = 0
                for i in range(0, len(rows[t]), batch_size):
                    inserted += sink.insert_rows(t, spec, rows[t][i:i + batch_size])
                report["tables"][t] = {"source_rows": len(rows[t]), "inserted": inserted,
                                       "skipped_existing": len(rows[t]) - inserted}
                progress(f"  loaded {t}: {inserted}/{len(rows[t])} inserted")
    except Exception as e:
        report["tables"] = {}
        return fail("FAILED_LOAD", f"load failed and was rolled back: {type(e).__name__}: {e}")
    return fail("COMPLETED", "all tables loaded; run verification next")
