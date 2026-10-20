import ast, importlib.util, unittest
from app.orm.generate import generate_models, change_report, class_name
from tests.fixtures import frozen_pipeline, make_pipeline
from app.schema_map.schema_map import SchemaMapError

class TestORMGeneration(unittest.TestCase):
    def setUp(self): self.p = frozen_pipeline(); self.sm = self.p.load_map(); self.src = generate_models(self.sm)
    def test_valid_python_with_models_and_relationships(self):
        ast.parse(self.src)
        for cls in ("class Customer(Base)", "class Product(Base)", "class Order(Base)", "class OrderItem(Base)"): self.assertIn(cls, self.src)
        self.assertIn("relationship(", self.src); self.assertIn("ForeignKey(", self.src); self.assertIn("UniqueConstraint('order_id', 'position')", self.src)
    def test_requires_frozen_and_deterministic(self):
        self.assertEqual(self.src, generate_models(self.sm))
        p = make_pipeline(); p.run_inspection(); p.infer(); p.similarity(); p.create_draft_map()
        with self.assertRaises(SchemaMapError): generate_models(p.load_map())
    def test_change_report_lists_mappings(self):
        r = change_report(self.sm); self.assertIn("address.postalCode", r); self.assertIn("items[].unitPrice", r)
    def test_class_names(self): self.assertEqual((class_name("orders_items"), class_name("customers")), ("OrderItem", "Customer"))

@unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "sqlalchemy not installed")
class TestORMCrudSQLite(unittest.TestCase):
    """CRUD logic through the generated models on SQLite (unit-level; PostgreSQL is covered by the integration test)."""
    def test_crud(self):
        import datetime as dt
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import StaticPool
        from app.orm.repository import Repository
        p = frozen_pipeline(); path = p.store.generated / "orm_models.py"
        from app.orm.loader import load_models
        m = load_models(path)
        eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        @event.listens_for(eng, "connect")
        def attach(conn, _): conn.execute("ATTACH DATABASE ':memory:' AS schemashift_demo")
        m.Base.metadata.create_all(eng)
        with Session(eng) as s:
            r = Repository(s, m.Customer)
            r.create(id="a" * 24, full_name="T", email="t@x.io", created_at=dt.datetime.now(dt.timezone.utc),
                     address_city="Pune", address_state="MH", address_postal_code="411001")
            self.assertEqual(r.get("a" * 24).full_name, "T")
            r.update("a" * 24, phone="123"); self.assertEqual(r.get("a" * 24).phone, "123")
            self.assertTrue(r.delete("a" * 24)); self.assertIsNone(r.get("a" * 24)); self.assertFalse(r.delete("a" * 24))

_HAS_API = importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx")
@unittest.skipUnless(_HAS_API, "fastapi/httpx not installed (pip install -r requirements.txt)")
class TestAPI(unittest.TestCase):
    def client(self, **kw):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(make_pipeline(**kw)))
    def test_health_and_collections(self):
        c = self.client(); self.assertEqual(c.get("/health").json()["status"], "ok")
        self.assertEqual(c.get("/api/v1/source/collections").json()["collections"], ["customers", "orders", "products"])
    def test_reports_404_before_run(self):
        c = self.client()
        for u in ("/api/v1/inspection/report", "/api/v1/schema-map", "/api/v1/similarity/report", "/api/v1/verification/report", "/api/v1/orm/models"):
            self.assertEqual(c.get(u).status_code, 404, u)
    def test_full_flow(self):
        import time
        c = self.client()
        self.assertEqual(c.post("/api/v1/inspection/run").status_code, 200)
        r = c.post("/api/v1/schema/infer").json(); self.assertEqual(r["pending_reviews"], 0)
        self.assertEqual(c.post("/api/v1/migration/run").status_code, 409)               # not frozen/generated yet
        self.assertEqual(c.post("/api/v1/artifacts/generate").status_code, 409)
        self.assertEqual(c.post("/api/v1/schema/freeze", json={"approved_by": "me"}).status_code, 200)
        self.assertEqual(c.post("/api/v1/artifacts/generate").status_code, 200)
        self.assertEqual(c.post("/api/v1/migration/run").status_code, 202)
        for _ in range(100):
            st = c.get("/api/v1/migration/status").json()
            if st["state"] != "running": break
            time.sleep(0.05)
        self.assertEqual(st["state"], "completed")
        self.assertEqual(c.post("/api/v1/verification/run").json()["status"], "PASS")
        self.assertIn("class Customer", c.get("/api/v1/orm/models").text)
    def test_freeze_blocked_with_pending_and_validation(self):
        c = self.client(threshold=0.9999); c.post("/api/v1/inspection/run"); c.post("/api/v1/schema/infer")
        self.assertEqual(c.post("/api/v1/schema/freeze", json={"approved_by": "me"}).status_code, 409)
        self.assertEqual(c.post("/api/v1/schema/approve", json={"decisions": []}).status_code, 422)
        self.assertEqual(c.post("/api/v1/schema/approve", json={"decisions": [{"table": "x", "column": "y", "action": "accept"}]}).status_code, 409)
    def test_no_destructive_endpoints(self):
        from starlette.routing import Route
        app = self.client().app
        paths = " ".join(r.path for r in app.routes if isinstance(r, Route))
        self.assertNotIn("rollback", paths); self.assertNotIn("cleanup", paths)
