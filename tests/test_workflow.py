import importlib.util, unittest
from app.ai import MockProvider
from app.workflow.nodes import run_sequential, next_stage, STAGES
from tests.fixtures import make_pipeline

APPROVE = lambda p: {"approve": True, "approved_by": "tester"}
def accept_all(p):
    return {"approve": True, "approved_by": "tester",
            "decisions": [{"table": x["table"], "column": x["column"], "action": "accept"} for x in p["pending"]]}

class TestRouting(unittest.TestCase):
    def test_error_goes_to_final_report(self):
        for s in STAGES[:-1]: self.assertEqual(next_stage(s, {"error": "x"}), "final_report")
    def test_pending_loops_approval(self):
        self.assertEqual(next_stage("human_approval", {"pending": [1]}), "human_approval")
        self.assertEqual(next_stage("human_approval", {"pending": []}), "freeze_map")
    def test_order(self):
        self.assertEqual(next_stage("freeze_map", {}), "generate_artifacts"); self.assertEqual(next_stage("final_report", {}), "END")

class TestWorkflowLogic(unittest.TestCase):
    def test_success(self):
        s = run_sequential(make_pipeline(), MockProvider(), APPROVE)
        self.assertEqual((s["status"], s["verification_status"], s["migration_status"]), ("SUCCESS", "PASS", "COMPLETED"))
    def test_rejection_stops_before_freeze_and_migration(self):
        p = make_pipeline(); s = run_sequential(p, MockProvider(), lambda x: {"approve": False})
        self.assertEqual(s["status"], "REJECTED")
        self.assertEqual(p.load_map()["status"], "draft"); self.assertEqual(sum(p.sink.count(t) for t in p.sink.data), 0)
    def test_low_confidence_cannot_skip_review(self):
        p = make_pipeline(threshold=0.9999)
        s = run_sequential(p, MockProvider(), lambda x: {"approve": True, "approved_by": "t"})   # approves but accepts nothing
        self.assertNotEqual(s["status"], "SUCCESS")
        self.assertEqual(p.load_map()["status"], "draft")
    def test_review_rounds_then_success(self):
        s = run_sequential(make_pipeline(threshold=0.9999), MockProvider(), accept_all)
        self.assertEqual(s["status"], "SUCCESS"); self.assertGreaterEqual(s["approval_rounds"], 1)
    def test_migration_failure_is_failed_status(self):
        from sample_data.generate import generate_seed
        docs = generate_seed(); p = make_pipeline(docs=docs); orig = p.run_inspection
        def late(): r = orig(); docs["products"][0]["stockQuantity"] = "many"; return r
        p.run_inspection = late
        s = run_sequential(p, MockProvider(), APPROVE)
        self.assertEqual(s["status"], "FAILED"); self.assertEqual(s["migration_status"], "FAILED_TRANSFORM"); self.assertNotIn("verification_status", s)
    def test_failed_verification_is_failed_status(self):
        p = make_pipeline(); orig = p.migrate
        def corrupt(progress=print):
            r = orig(progress); k = next(iter(p.sink.data["products"])); p.sink.data["products"][k]["name"] = "TAMPERED"; return r
        p.migrate = corrupt
        s = run_sequential(p, MockProvider(), APPROVE)
        self.assertEqual((s["status"], s["verification_status"]), ("FAILED", "FAIL"))
        self.assertIn("FAILED", p.store.final_report.read_text())
    def test_ai_failure_does_not_break_pipeline(self):
        class Broken(MockProvider):
            def explain_field(self, f): raise RuntimeError("boom")
        p = make_pipeline()
        s = run_sequential(p, Broken(), APPROVE)
        self.assertEqual(s["status"], "SUCCESS")
        failures = [note for note in s["ai_notes"] if not note["ok"]]
        self.assertTrue(failures)
        self.assertIn("unexpected advisory error", failures[0]["error"])
        self.assertIn("request failed", p.store.final_report.read_text())

    def test_frozen_map_reused_without_error(self):
        """A pipeline with an already-frozen map must succeed without PipelineError."""
        from tests.fixtures import frozen_pipeline
        # frozen_pipeline() already ran inspect/infer/similarity/freeze/generate.
        # run_sequential must reuse the frozen map and proceed to migration+verification.
        p = frozen_pipeline()
        s = run_sequential(p, MockProvider(), APPROVE)
        self.assertEqual(s["status"], "SUCCESS")
        self.assertIn("reusing valid frozen map", " ".join(s["log"]))

    def test_tampered_frozen_map_fails_safely(self):
        """A frozen map whose hash has been invalidated must route to FAILED, not crash."""
        from tests.fixtures import frozen_pipeline
        p = frozen_pipeline()
        sm = p.load_map()
        # Tamper with the map content without updating the hash.
        sm["tables"]["customers"]["columns"]["email"]["sql_type"] = "INTEGER"
        p.save_map(sm)
        s = run_sequential(p, MockProvider(), APPROVE)
        self.assertEqual(s["status"], "FAILED")
        self.assertIn("hash", s.get("error", "").lower())

@unittest.skipUnless(importlib.util.find_spec("langgraph"), "langgraph not installed (pip install -r requirements.txt)")
class TestLangGraph(unittest.TestCase):
    def test_interrupt_resume_success(self):
        from app.workflow.graph import run_workflow
        seen = []
        def approver(payload): seen.append(payload); return {"approve": True, "approved_by": "tester"}
        s = run_workflow(make_pipeline(), MockProvider(), approver, thread_id="t1")
        self.assertEqual(s["status"], "SUCCESS"); self.assertEqual(len(seen), 1)
    def test_rejection(self):
        from app.workflow.graph import run_workflow
        self.assertEqual(run_workflow(make_pipeline(), MockProvider(), lambda p: {"approve": False}, thread_id="t2")["status"], "REJECTED")
    def test_pauses_at_approval_before_freeze(self):
        from app.workflow.graph import build_graph, get_checkpointer
        p = make_pipeline(); g = build_graph(p, MockProvider(), get_checkpointer(p.st)); cfg = {"configurable": {"thread_id": "t3"}}
        g.invoke({"status": "RUNNING", "log": [], "ai_notes": []}, cfg)
        self.assertEqual(g.get_state(cfg).next, ("human_approval",)); self.assertEqual(p.load_map()["status"], "draft")
