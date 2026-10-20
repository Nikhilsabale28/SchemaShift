"""Workflow node logic (plain functions over the Pipeline). graph.py wires these into LangGraph; the same
functions and routing table are used by `run_sequential`, a dependency-free runner used for unit tests."""
from __future__ import annotations
from ..pipeline import Pipeline
from ..schema_map.schema_map import pending_reviews
from ..utils.store import Store
from ..utils.log import get_logger

log = get_logger("schemashift.workflow")

STAGES = ["inspect_source", "infer_schema", "calculate_similarity", "mismatch_report", "human_approval",
          "freeze_map", "generate_artifacts", "migrate_data", "verify_data", "final_report"]
MAX_APPROVAL_ROUNDS = 20

def next_stage(current: str, state: dict) -> str:
    """Routing table. Any error short-circuits to final_report; approval loops until nothing is pending."""
    if current == "final_report":
        return "END"
    if state.get("error"):
        return "final_report"
    if current == "human_approval":
        if state.get("pending"):
            return "human_approval"
        return "freeze_map"
    return STAGES[STAGES.index(current) + 1]

class Nodes:
    def __init__(self, pipeline: Pipeline, ai):
        self.p, self.ai = pipeline, ai

    def _ai_note(self, method, *args):
        try:
            return method(*args).as_dict()
        except Exception as exc:
            log.warning("AI advisory failed unexpectedly (%s)", type(exc).__name__)
            return {
                "provider": getattr(self.ai, "name", "unknown"),
                "label": "AI advisory failed; deterministic workflow remains authoritative",
                "ok": False,
                "content": {},
                "error": f"unexpected advisory error ({type(exc).__name__})",
                "advisory": True,
            }

    def _guard(stage):
        def deco(fn):
            def wrapper(self, state, *args):
                try:
                    out = fn(self, state, *args) or {}
                    out.setdefault("stage", stage)
                    return out
                except Exception as e:
                    return {"error": f"{stage}: {type(e).__name__}: {e}", "stage": stage, "status": "FAILED",
                            "log": [f"{stage} FAILED: {e}"]}
            wrapper.__name__ = stage
            return wrapper
        return deco

    @_guard("inspect_source")
    def inspect_source(self, state):
        rep = self.p.run_inspection()
        return {"log": [f"inspected {len(rep['collections'])} collections"]}

    @_guard("infer_schema")
    def infer_schema(self, state):
        prop = self.p.infer()
        return {"log": [f"inferred {len(prop['tables'])} tables"]}

    @_guard("calculate_similarity")
    def calculate_similarity(self, state):
        rep = self.p.similarity()
        # Reuse a valid frozen map; only create a draft when none exists or a new revision is forced.
        if state.get("force_new_map"):
            self.p.create_draft_map(force=True)
        elif self.p.store.exists(self.p.store.schema_map):
            existing = self.p.load_map()
            if existing.get("status") == "frozen":
                try:
                    from ..schema_map.schema_map import require_frozen
                    require_frozen(existing)   # validates hash; raises if tampered
                    return {"overall_percent": rep["overall_percent"],
                            "log": [f"reusing valid frozen map (hash {existing['content_hash'][:12]})",
                                    f"overall match {rep['overall_percent']}%"]}
                except Exception as e:
                    return {"error": f"calculate_similarity: frozen map hash invalid: {e}",
                            "stage": "calculate_similarity", "status": "FAILED",
                            "log": [f"calculate_similarity FAILED: {e}"]}
            # draft exists — leave it as-is (no re-creation needed)
        else:
            self.p.create_draft_map(force=False)
        return {"overall_percent": rep["overall_percent"], "log": [f"overall match {rep['overall_percent']}%"]}

    @_guard("mismatch_report")
    def mismatch_report(self, state):
        sm = self.p.load_map()
        # If the map is already frozen and valid, no review is needed.
        if sm.get("status") == "frozen":
            from ..schema_map.schema_map import require_frozen
            try:
                require_frozen(sm)
                return {"pending": [], "ai_notes": [], "log": ["map already frozen; skipping review"]}
            except Exception:
                pass  # tampered — fall through to normal review path
        pend = pending_reviews(sm)
        sim = Store.read_json(self.p.store.similarity_json)
        worst = sorted(sim["fields"], key=lambda f: (f["score"], f["target"]))[:3]
        notes = [self._ai_note(self.ai.explain_field, field) for field in worst]
        notes.append(self._ai_note(
            self.ai.summarize_risks,
            {"needs_review": len(pend), "unmatched": len(sim["unmatched_source_fields"]),
             "overall_percent": sim["overall_percent"]},
        ))
        failures = sum(not note.get("ok") for note in notes)
        ai_log = [f"{failures} AI advisory call(s) failed; deterministic decisions are unchanged"] if failures else []
        return {"pending": pend, "ai_notes": notes,
                "log": [f"{len(pend)} mapping(s) need review", *ai_log]}

    @_guard("human_approval")
    def apply_approval(self, state, resume: dict):
        """Called with the human's resume value (after interrupt)."""
        if not isinstance(resume, dict) or resume.get("approve") is not True:
            return {"status": "REJECTED", "error": "approval rejected or not given by the reviewer",
                    "log": ["human approval REJECTED"]}
        rounds = int(state.get("approval_rounds", 0)) + 1
        if rounds > MAX_APPROVAL_ROUNDS:
            return {"error": "too many approval rounds", "log": ["approval loop aborted"]}
        if resume.get("decisions"):
            self.p.apply_decisions(resume["decisions"])
        pend = pending_reviews(self.p.load_map())
        return {"pending": pend, "approval_rounds": rounds, "approved_by": str(resume.get("approved_by") or "reviewer"),
                "log": [f"approval round {rounds}: {len(pend)} still pending"]}

    def approval_payload(self, state) -> dict:
        sm = self.p.load_map()
        return {"overall_percent": state.get("overall_percent"), "map_revision": sm["revision"],
                "pending": pending_reviews(sm), "instructions": "resume with {'approve': true, 'approved_by': name, 'decisions': [...]}"}

    @_guard("freeze_map")
    def freeze_map(self, state):
        sm = self.p.load_map()
        if sm.get("status") == "frozen":
            from ..schema_map.schema_map import require_frozen
            h = require_frozen(sm)   # re-validates hash
            return {"map_hash": h, "log": [f"map already frozen {h[:12]}"]}
        sm = self.p.freeze(state.get("approved_by", "reviewer"))
        return {"map_hash": sm["content_hash"], "log": [f"froze map {sm['content_hash'][:12]}"]}

    @_guard("generate_artifacts")
    def generate_artifacts(self, state):
        m = self.p.generate_artifacts()
        return {"log": [f"generated {len(m['files'])} artifacts"]}

    @_guard("migrate_data")
    def migrate_data(self, state):
        rep = self.p.migrate(progress=lambda *a: None)
        out = {"migration_status": rep["status"], "log": [f"migration {rep['status']}"]}
        if rep["status"] != "COMPLETED":
            out.update({"error": f"migration {rep['status']}: {rep['message']}", "status": "FAILED"})
            if rep["errors"]:
                note = self._ai_note(self.ai.diagnose_failure, rep["errors"])
                out["ai_notes"] = [note]
                if not note.get("ok"):
                    out["log"].append("AI failure diagnosis unavailable; migration failure remains authoritative")
        return out

    @_guard("verify_data")
    def verify_data(self, state):
        rep = self.p.verify()
        out = {"verification_status": rep["status"], "log": [f"verification {rep['status']}"]}
        if rep["status"] != "PASS":
            out.update({"error": "verification FAILED - target data must not be used", "status": "FAILED"})
        return out

    @_guard("final_report")
    def final_report(self, state):
        if state.get("status") == "REJECTED":
            final = "REJECTED"
        elif state.get("error") or state.get("verification_status") != "PASS":
            final = "FAILED"
        else:
            final = "SUCCESS"
        lines = ["# SchemaShift workflow report", "", f"**Final status: {final}**", "",
                 f"- overall match: {state.get('overall_percent')}%", f"- schema map hash: {state.get('map_hash')}",
                 f"- migration: {state.get('migration_status')}", f"- verification: {state.get('verification_status')}",
                 f"- error: {state.get('error') or 'none'}", "", "## Log", ""] + [f"- {x}" for x in state.get("log", [])]
        ai = state.get("ai_notes", [])
        if ai:
            lines += ["", "## AI advisory notes (not authoritative)", ""]
            for note in ai:
                if note.get("ok"):
                    lines.append(f"- {note.get('label')}: {note.get('content')}")
                else:
                    lines.append(
                        f"- {note.get('provider', 'AI')}: request failed — "
                        f"{note.get('error', 'no advisory result returned')}"
                    )
        self.p.store.final_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"status": final, "log": ["final report written"]}

def run_sequential(pipeline: Pipeline, ai, approver, initial: dict | None = None) -> dict:
    """Dependency-free runner using the SAME nodes and routing as the LangGraph graph (used by tests)."""
    n = Nodes(pipeline, ai)
    state = {"status": "RUNNING", "log": []}
    state.update(initial or {})
    def merge(upd):
        for k, v in upd.items():
            state[k] = (state.get(k, []) + v) if k == "log" else v
    stage = "inspect_source"
    while stage != "END":
        if stage == "human_approval":
            merge(n.apply_approval(state, approver(n.approval_payload(state))))
        else:
            merge(getattr(n, stage)(state))
        stage = next_stage(stage, state)
    return state
