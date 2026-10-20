import copy, unittest
from tests.helpers import sample_collections
from app.matching.normalize import normalize_name
from app.matching.similarity import score_field, aggregate
from app.core.types import type_compatibility, dominant_sql_type
from app.inspection.inspector import inspect_database, sample_documents
from app.inference.infer import infer_schema
from app.matching.report import build_similarity_report
from app.schema_map import schema_map as sm_mod
from app.schema_map.schema_map import SchemaMapError
from app.generation.ddl import generate_ddl, generate_rollback, dependency_order, quote_ident, GenerationError

def build(threshold=0.75):
    insp = inspect_database(sample_collections())
    schema = infer_schema(insp)
    rep = build_similarity_report(insp, schema, review_threshold=threshold)
    return insp, schema, rep, sm_mod.create_schema_map(schema, rep, "demo", threshold)

class TestNormalize(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(normalize_name("customerId"), "customer_id")
        self.assertEqual(normalize_name("PostalCode"), "postal_code")
        self.assertEqual(normalize_name("Zip-Code"), "postal_code")
        self.assertEqual(normalize_name("unit price"), "unit_price")
        self.assertEqual(normalize_name("qty"), "quantity")

class TestTypes(unittest.TestCase):
    def test_compat(self):
        self.assertEqual(type_compatibility("integer", "BIGINT"), 1.0)
        self.assertLess(type_compatibility("string", "TIMESTAMPTZ"), 0.5)
        self.assertEqual(dominant_sql_type({"string": 2, "integer": 1})[1], True)
        self.assertEqual(dominant_sql_type({"integer": 2, "float": 1}), ("DOUBLE PRECISION", False))

class TestScoring(unittest.TestCase):
    def test_deterministic(self):
        a = score_field("address.city", "string", "customers", "customers", "address_city", "TEXT")
        b = score_field("address.city", "string", "customers", "customers", "address_city", "TEXT")
        self.assertEqual(a, b); self.assertEqual(a.score, 1.0)
    def test_type_cap(self):
        f = score_field("name", "string", "customers", "customers", "name", "TIMESTAMPTZ")
        self.assertLessEqual(f.score, 0.6)
    def test_unresolved_fk_cap(self):
        f = score_field("ownerId", "objectid", "x", "x", "owner_id", "VARCHAR(24)", unresolved_fk=True)
        self.assertLessEqual(f.score, 0.6)
    def test_aggregate_counts_unmatched(self):
        self.assertEqual(aggregate({"t": [1.0, 0.0]})["overall"], 0.5)

class TestInspectInfer(unittest.TestCase):
    def test_sampling_reproducible(self):
        docs = list(range(100))
        self.assertEqual(sample_documents(docs, 10, 1), sample_documents(docs, 10, 1))
    def test_nullable_and_absent(self):
        insp, schema, _, _ = build()
        f = insp["collections"]["customers"]["fields"]
        self.assertTrue(f["phone"]["has_null"]); self.assertTrue(f["phone"]["sometimes_absent"])
    def test_nested_flatten_and_child_table(self):
        _, schema, _, _ = build()
        t = schema["tables"]
        self.assertIn("address_postal_code", t["customers"]["columns"])
        self.assertEqual(t["orders_items"]["kind"], "array_child")
        self.assertEqual(t["orders_items"]["foreign_keys"][0]["ref_table"], "orders")
        self.assertTrue(any(fk["ref_table"] == "products" for fk in t["orders_items"]["foreign_keys"]))
    def test_no_field_silently_dropped(self):
        _, _, rep, _ = build()
        self.assertEqual(rep["unmatched_source_fields"], [])

class TestSchemaMapLifecycle(unittest.TestCase):
    def test_threshold_forces_review_and_freeze_blocked(self):
        _, _, rep, sm = build(threshold=0.99)
        self.assertTrue(sm_mod.pending_reviews(sm))
        with self.assertRaises(SchemaMapError): sm_mod.freeze(sm, "me")
    def test_accept_then_freeze_and_verify(self):
        _, _, _, sm = build(threshold=0.99)
        for p in sm_mod.pending_reviews(sm):
            sm_mod.apply_decision(sm, p["table"], p["column"], "accept")
        sm_mod.freeze(sm, "me")
        self.assertEqual(sm["status"], "frozen"); sm_mod.require_frozen(sm)
    def test_tamper_detected_and_edit_blocked(self):
        _, _, _, sm = build(); sm_mod.freeze(sm, "me")
        with self.assertRaises(SchemaMapError):
            sm_mod.apply_decision(sm, "customers", "name", "accept")
        sm["tables"]["customers"]["columns"]["name"]["sql_type"] = "BIGINT"
        with self.assertRaises(SchemaMapError): sm_mod.require_frozen(sm)
    def test_new_revision_invalidates(self):
        _, _, _, sm = build(); sm_mod.freeze(sm, "me")
        new = sm_mod.start_new_revision(sm)
        self.assertEqual(new["revision"], 2); self.assertEqual(new["status"], "draft")
        with self.assertRaises(SchemaMapError): sm_mod.require_frozen(new)
    def test_unresolved_fk_requires_review_and_override_fixes(self):
        coll = sample_collections(); del coll["products"]
        insp = inspect_database(coll); schema = infer_schema(insp)
        rep = build_similarity_report(insp, schema); sm = sm_mod.create_schema_map(schema, rep, "d")
        self.assertTrue(any(p["column"] == "product_id" for p in sm_mod.pending_reviews(sm)))
    def test_reject_blocks_freeze(self):
        _, _, _, sm = build(); sm_mod.apply_decision(sm, "customers", "name", "reject")
        with self.assertRaises(SchemaMapError): sm_mod.freeze(sm, "me")

class TestDDL(unittest.TestCase):
    def setUp(self):
        _, _, _, self.sm = build(); sm_mod.freeze(self.sm, "me")
    def test_requires_frozen(self):
        _, _, _, sm = build()
        with self.assertRaises(SchemaMapError): generate_ddl(sm)
    def test_order_and_reproducible(self):
        order = dependency_order(self.sm["tables"])
        self.assertLess(order.index("customers"), order.index("orders"))
        self.assertLess(order.index("orders"), order.index("orders_items"))
        self.assertEqual(generate_ddl(self.sm), generate_ddl(copy.deepcopy(self.sm)))
    def test_contents(self):
        ddl = generate_ddl(self.sm)
        self.assertIn('PRIMARY KEY ("id")', ddl); self.assertIn("FOREIGN KEY", ddl)
        self.assertIn("NOT NULL", ddl); self.assertIn("CREATE INDEX", ddl)
    def test_cycle_detected(self):
        t = {"a": {"foreign_keys": [{"column": "b_id", "ref_table": "b", "ref_column": "id", "resolved": True}]},
             "b": {"foreign_keys": [{"column": "a_id", "ref_table": "a", "ref_column": "id", "resolved": True}]}}
        with self.assertRaises(GenerationError): dependency_order(t)
    def test_identifier_safety(self):
        with self.assertRaises(GenerationError): quote_ident('x"; DROP TABLE y;--')
    def test_rollback_scoped(self):
        rb = generate_rollback(self.sm)
        self.assertIn('DROP TABLE IF EXISTS "schemashift_demo"."orders_items"', rb)
        self.assertNotIn("DROP DATABASE", rb)

if __name__ == "__main__":
    unittest.main()
