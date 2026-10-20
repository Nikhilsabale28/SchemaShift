"""Similarity report built from the inspection report + inferred schema.

Everything here is recomputable from stored metadata (no AI involved).
Unmatched source fields (observed in Mongo but absent from the target) score 0.
"""
from __future__ import annotations
from .similarity import score_field, aggregate, Weights
from ..core.types import type_compatibility

def _src_type(col: dict) -> str:
    kinds = {k: v for k, v in col.get("source_types", {}).items() if k != "null"}
    if not kinds:
        return "string"
    return sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

def build_similarity_report(inspection: dict, schema: dict, *, weights: Weights = Weights(),
                            abbreviations=None, review_threshold: float = 0.75) -> dict:
    fields, table_scores, covered = [], {}, set()
    for tname in sorted(schema["tables"]):
        t = schema["tables"][tname]
        table_scores.setdefault(tname, [])
        for cname in sorted(t["columns"]):
            c = t["columns"][cname]
            if c["source_path"] is None:          # generated key (BIGSERIAL), no source field
                continue
            covered.add((t["source_collection"], c["source_path"]))
            if c.get("transform") == "parent_key":   # derived structurally from the array's parent
                fields.append({"source_path": f"{t['array_path']}[] (parent _id)",
                               "target": f"{tname}.{cname}", "name": 1.0, "type": 1.0, "structure": 1.0,
                               "score": 1.0, "status": "matched",
                               "notes": ["parent key derived from array parent; not name-matched"],
                               "table": tname, "column": cname, "needs_review": False,
                               "review_reasons": []})
                table_scores[tname].append(1.0)
                continue
            unresolved = bool(c.get("unresolved_fk"))
            fs = score_field(c["source_path"], _src_type(c), t["source_collection"], tname, cname,
                             c["sql_type"], is_array_child=t["kind"] == "array_child",
                             unresolved_fk=unresolved, weights=weights, abbreviations=abbreviations)
            reasons = review_reasons(fs.score, c, review_threshold)
            row = fs.as_dict()
            row.update({"table": tname, "column": cname, "needs_review": bool(reasons),
                        "review_reasons": reasons})
            fields.append(row)
            table_scores[tname].append(fs.score)
    unmatched = []
    for cname, coll in sorted(inspection["collections"].items()):
        for path, info in sorted(coll["fields"].items()):
            if info["is_object"] and not info["is_array"] and set(info["types"]) <= {"object", "null"}:
                continue       # container; leaves are mapped individually
            if info["is_array"]:
                continue       # becomes child table
            key = (cname, path)
            if key not in covered and not any(k[0] == cname and k[1].startswith(path + "[]") for k in covered) \
               and "[]" not in path:
                unmatched.append({"collection": cname, "source_path": path, "score": 0.0,
                                  "explanation": "observed in source but not mapped to any target column"})
                tbl = next((n for n, t in schema["tables"].items()
                            if t["source_collection"] == cname and t["kind"] == "root"), None)
                if tbl:
                    table_scores[tbl].append(0.0)
    agg = aggregate(table_scores)
    counts = {"matched": 0, "guessed": 0, "unmatched": len(unmatched), "needs_review": 0}
    for f in fields:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
        counts["needs_review"] += 1 if f["needs_review"] else 0
    return {"similarity_version": 1,
            "weights": {"name": weights.name, "type": weights.type, "structure": weights.structure},
            "review_threshold": review_threshold,
            "aggregation": ("table = mean(field scores incl. unmatched=0); "
                            "overall = sum(all field scores)/count(all fields)"),
            "overall_percent": round(agg["overall"] * 100, 2),
            "per_table_percent": {k: round(v * 100, 2) for k, v in agg["per_table"].items()},
            "counts": counts, "fields": fields, "unmatched_source_fields": unmatched,
            "low_confidence": [f for f in fields if f["score"] < review_threshold]}

def review_reasons(score: float, col: dict, threshold: float) -> list:
    r = []
    if score < threshold:
        r.append(f"confidence {score:.2f} below threshold {threshold:.2f}")
    if col.get("mixed_types"):
        r.append("inconsistent source types require an explicit conversion decision")
    if col.get("unresolved_fk"):
        r.append("unresolved foreign key")
    return r

def to_markdown(rep: dict) -> str:
    out = ["# SchemaShift similarity report", "",
           f"**Overall match: {rep['overall_percent']}%**", "",
           f"Weights: {rep['weights']}  |  review threshold: {rep['review_threshold']}", "",
           f"Aggregation: {rep['aggregation']}", "", "## Per table", "",
           "| Table | Match % |", "|---|---|"]
    out += [f"| {t} | {p} |" for t, p in rep["per_table_percent"].items()]
    out += ["", f"Counts: {rep['counts']}", "", "## Fields", "",
            "| Source path | Target | Name | Type | Struct | Score | Status | Review |",
            "|---|---|---|---|---|---|---|---|"]
    for f in rep["fields"]:
        out.append(f"| {f['source_path']} | {f['target']} | {f['name']} | {f['type']} | "
                   f"{f['structure']} | {f['score']} | {f['status']} | {'YES' if f['needs_review'] else ''} |")
    if rep["unmatched_source_fields"]:
        out += ["", "## Unmatched source fields", ""]
        out += [f"- {u['collection']}.{u['source_path']}: {u['explanation']}" for u in rep["unmatched_source_fields"]]
    return "\n".join(out) + "\n"
