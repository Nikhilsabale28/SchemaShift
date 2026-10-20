import copy, unittest
from decimal import Decimal
from app.migration.sink import InMemorySink, SinkError
from app.migration.runner import run_migration
from app.verification.verify import verify_migration
from app.schema_map import schema_map as S
from app.pipeline import PipelineError
from sample_data.generate import generate_seed
from tests.fixtures import frozen_pipeline, make_pipeline

class TestRunner(unittest.TestCase):
    def setUp(self):
        self.p = frozen_pipeline(); self.sm = self.p.load_map()
        self.ddl = (self.p.store.generated / "schema.sql").read_text()
    def run_it(self, source=None, sink=None, sm=None):
        return run_migration(sm or self.sm, source or self.p.source, sink or self.p.sink, self.ddl, progress=lambda *a: None)
    def test_success_counts(self):
        r = self.run_it()
        self.assertEqual(r["status"], "COMPLETED")
        self.assertEqual({t: v["inserted"] for t, v in r["tables"].items()},
                         {"customers": 10, "products": 8, "orders": 20, "orders_items": 39})
    def test_rerun_is_idempotent(self):
        self.run_it(); r2 = self.run_it()
        self.assertEqual(r2["status"], "COMPLETED")
        self.assertTrue(all(v["inserted"] == 0 and v["skipped_existing"] == v["source_rows"] for v in r2["tables"].values()))
        self.assertEqual(self.p.sink.count("orders_items"), 39)
    def test_requires_frozen_map(self):
        draft = copy.deepcopy(self.sm); draft["status"] = "draft"
        self.assertEqual(self.run_it(sm=draft)["status"], "FAILED_PRECONDITION")
    def test_tampered_map_rejected(self):
        bad = copy.deepcopy(self.sm); bad["tables"]["products"]["columns"]["name"]["sql_type"] = "BIGINT"
        self.assertEqual(self.run_it(sm=bad)["status"], "FAILED_PRECONDITION")
    def test_transform_failure_writes_nothing(self):
        docs = generate_seed(); docs["products"][2]["stockQuantity"] = "lots"; docs["orders"][0]["extra"] = 1
        from app.inspection.source import InMemorySource
        sink = InMemorySink()
        r = self.run_it(source=InMemorySource(docs), sink=sink)
        self.assertEqual(r["status"], "FAILED_TRANSFORM"); self.assertEqual(len(r["errors"]), 2)
        self.assertTrue(all(sink.count(t) == 0 for t in sink.data))
        self.assertTrue(all("document_id" in e and "path" in e for e in r["errors"]))
    def test_load_failure_rolls_back_everything(self):
        docs = generate_seed(); docs["orders"][3]["customerId"] = type(docs["orders"][3]["customerId"])("f" * 24)  # dangling ref
        from app.inspection.source import InMemorySource
        sink = InMemorySink()
        r = self.run_it(source=InMemorySource(docs), sink=sink)
        self.assertEqual(r["status"], "FAILED_LOAD"); self.assertIn("FOREIGN KEY", r["message"])
        self.assertEqual(sum(sink.count(t) for t in sink.data), 0)   # customers/products rolled back too
    def test_missing_collection_is_precondition_failure(self):
        from app.inspection.source import InMemorySource
        docs = generate_seed(); del docs["products"]
        self.assertEqual(self.run_it(source=InMemorySource(docs))["status"], "FAILED_PRECONDITION")

class TestVerification(unittest.TestCase):
    def setUp(self):
        self.p = frozen_pipeline(); self.sm = self.p.load_map()
        self.assertEqual(self.p.migrate(progress=lambda *a: None)["status"], "COMPLETED")
    def check(self): return verify_migration(self.sm, self.p.source, self.p.sink)
    def test_pass(self):
        r = self.check(); self.assertEqual(r["status"], "PASS"); self.assertEqual(r["summary"]["failed"], 0)
        names = [c["name"] for c in r["checks"]]
        self.assertTrue(any(n.endswith(":foreign_key") for n in names)); self.assertTrue(any(n.endswith(":checksum") for n in names))
    def test_missing_row_fails(self):
        k = next(iter(self.p.sink.data["customers"])); 
        # remove an order first to keep FK integrity, then remove nothing else: count mismatch on orders
        k = next(iter(self.p.sink.data["orders_items"])); del self.p.sink.data["orders_items"][k]
        r = self.check(); self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any(c["name"] == "orders_items:count" and not c["passed"] for c in r["checks"]))
        self.assertIn("recovery", r)
    def test_critical_value_mismatch_fails(self):
        k = next(iter(self.p.sink.data["customers"])); self.p.sink.data["customers"][k]["email"] = "evil@example.com"
        r = self.check(); self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any(c["name"] == "customers:critical_fields" and not c["passed"] for c in r["checks"]))
    def test_noncritical_value_mismatch_still_fails_checksum(self):
        k = next(iter(self.p.sink.data["customers"])); self.p.sink.data["customers"][k]["address_city"] = "Nowhere"
        self.assertEqual(self.check()["status"], "FAIL")
    def test_orphan_foreign_key_detected(self):
        k = next(iter(self.p.sink.data["orders"])); self.p.sink.data["orders"][k]["customer_id"] = "0" * 24
        r = self.check(); self.assertTrue(any(c["name"].endswith("customer_id->customers:foreign_key") and not c["passed"] for c in r["checks"]))
    def test_wrong_type_detected(self):
        k = next(iter(self.p.sink.data["products"])); self.p.sink.data["products"][k]["price"] = "free"
        self.assertEqual(self.check()["status"], "FAIL")
    def test_null_in_required_column_detected(self):
        k = next(iter(self.p.sink.data["products"])); self.p.sink.data["products"][k]["name"] = None
        r = self.check(); self.assertTrue(any(c["name"] == "products:required_fields" and not c["passed"] for c in r["checks"]))
    def test_unfrozen_map_cannot_pass(self):
        draft = copy.deepcopy(self.sm); draft["status"] = "draft"
        self.assertEqual(verify_migration(draft, self.p.source, self.p.sink)["status"], "FAIL")

class TestPipelineArtifacts(unittest.TestCase):
    def test_artifacts_reproducible(self):
        a, b = frozen_pipeline(), frozen_pipeline()
        for f in ("schema.sql", "rollback.sql", "orm_models.py", "etl_plan.json"):
            self.assertEqual((a.store.generated / f).read_text().replace(a.load_map()["content_hash"], "H"),
                             (b.store.generated / f).read_text().replace(b.load_map()["content_hash"], "H"))
    def test_revision_invalidates_artifacts_and_blocks_migration(self):
        p = frozen_pipeline(); self.assertTrue(p.artifacts_status()[0])
        p.begin_revision()
        self.assertFalse(p.artifacts_status()[0])
        self.assertEqual([f.name for f in p.store.generated.iterdir() if f.suffix != ".gitkeep"], [])
        with self.assertRaises(PipelineError): p.migrate()
    def test_modified_generated_file_detected(self):
        p = frozen_pipeline(); (p.store.generated / "schema.sql").write_text("DROP SCHEMA public CASCADE;")
        ok, why = p.artifacts_status(); self.assertFalse(ok); self.assertIn("schema.sql", why)
        with self.assertRaises(PipelineError): p.migrate()
    def test_frozen_map_not_silently_replaced(self):
        p = frozen_pipeline()
        with self.assertRaises(PipelineError): p.create_draft_map()
        p.create_draft_map(force=True); self.assertEqual(p.load_map()["status"], "draft")
        self.assertFalse(p.artifacts_status()[0])
    def test_generate_requires_freeze(self):
        p = make_pipeline(); p.run_inspection(); p.infer(); p.similarity(); p.create_draft_map()
        with self.assertRaises(S.SchemaMapError): p.generate_artifacts()
    def test_rollback_guards(self):
        import dataclasses
        p = frozen_pipeline()
        with self.assertRaises(PipelineError): p.rollback("schemashift_demo")      # disabled by default
        p.st = dataclasses.replace(p.st, allow_destructive_cleanup=True)
        with self.assertRaises(PipelineError): p.rollback("public")                # wrong schema confirmation
        p.migrate(progress=lambda *a: None); p.rollback("schemashift_demo")
        self.assertEqual(sum(p.sink.count(t) for t in p.sink.data), 0)
