"""Deterministic offline provider for development and tests. NOT a language model."""
from __future__ import annotations
from collections import Counter
from .base import AIProvider, AIResult, validate_field_row, summarize_errors
from ..matching.similarity import name_similarity

class MockProvider(AIProvider):
    name = "mock"
    is_llm = False

    def _ok(self, content): return AIResult(self.name, self.label(), True, content)

    def suggest_mappings(self, schema_summary):
        sugg = []
        tables = schema_summary.get("tables", {})
        for t, spec in sorted(tables.items()):
            for c in sorted(spec.get("columns", [])):
                if c.get("unresolved_fk"):
                    best = sorted(((name_similarity(c["name"], other), other) for other in tables if other != t),
                                  reverse=True)[:1]
                    sugg.append({"table": t, "column": c["name"], "type": "relationship",
                                 "suggestion": f"possible reference to table '{best[0][1]}'" if best else "no candidate table",
                                 "reason": "unresolved foreign key; closest table name by token similarity"})
                elif c.get("mixed_types"):
                    sugg.append({"table": t, "column": c["name"], "type": "type",
                                 "suggestion": "store as TEXT or clean the source data", "reason": "inconsistent source types"})
            if spec.get("kind") == "array_child":
                sugg.append({"table": t, "column": None, "type": "structure",
                             "suggestion": "keep as a child table", "reason": "array of objects needs one row per element"})
        return self._ok({"suggestions": sugg})

    def explain_field(self, field_row):
        r = validate_field_row(field_row)
        weak = min((("name", r["name"]), ("type", r["type"]), ("structure", r["structure"])), key=lambda x: (x[1], x[0]))
        text = (f"'{r['source_path']}' maps to '{r['target']}' with score {r['score']:.2f} "
                f"(name {r['name']:.2f}, type {r['type']:.2f}, structure {r['structure']:.2f}). "
                + ("All components are strong." if weak[1] >= 0.9 else f"The weakest component is {weak[0]} ({weak[1]:.2f})."))
        return self._ok({"explanation": text})

    def diagnose_failure(self, errors):
        errs = summarize_errors(errors)
        if not errs:
            return self._ok({"diagnosis": "No errors supplied.", "recommendations": []})
        groups = Counter((e["path"], e["message"]) for e in errs)
        recs = []
        for (path, msg), n in sorted(groups.items()):
            if "not in the frozen schema map" in msg:
                recs.append(f"{n} document(s) contain unmapped field(s) at {path}: start a new schema-map revision, map them, re-approve and re-freeze.")
            elif "NOT NULL" in msg:
                recs.append(f"{n} document(s) lack a required value at {path}: make the column nullable in a new revision or fix the data.")
            else:
                recs.append(f"{n} document(s) failed at {path}: {msg}. Consider overriding the target type in a new revision.")
        return self._ok({"diagnosis": f"{len(errs)} error(s) in {len(groups)} distinct pattern(s).", "recommendations": recs})

    def summarize_risks(self, summary):
        risks = []
        if summary.get("needs_review"): risks.append(f"{summary['needs_review']} mapping(s) need human review.")
        if summary.get("unmatched"): risks.append(f"{summary['unmatched']} source field(s) are unmapped.")
        if summary.get("overall_percent", 100) < 90: risks.append("Overall match is below 90%; review low-confidence fields.")
        if not risks: risks.append("No automatic risk indicators; still review the frozen map before migrating.")
        return self._ok({"risks": risks})
