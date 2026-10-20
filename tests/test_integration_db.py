"""Integration tests against REAL MongoDB + PostgreSQL. Skipped unless SCHEMASHIFT_INTEGRATION=1.
Run:  $env:SCHEMASHIFT_INTEGRATION="1"; python -m pytest tests/test_integration_db.py -v
They use the databases configured in .env (demo collections / schema only)."""
import dataclasses, os, tempfile, unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get("SCHEMASHIFT_INTEGRATION") == "1", "set SCHEMASHIFT_INTEGRATION=1 to run (needs MongoDB + PostgreSQL)")
class TestRealDatabases(unittest.TestCase):
    def test_full_migration_against_real_databases(self):
        from app.config import load_settings
        from app.pipeline import Pipeline
        from sample_data.generate import seed_mongo
        st = dataclasses.replace(load_settings(), base_dir=Path(tempfile.mkdtemp()), allow_destructive_cleanup=True)
        st.require_postgres_configured()
        seed_mongo(st.mongo_uri, st.mongo_db)
        p = Pipeline(st)
        p.run_inspection(); p.infer(); p.similarity(); p.create_draft_map(); p.freeze("integration-test"); p.generate_artifacts()
        try:
            self.assertEqual(p.migrate(progress=lambda *a: None)["status"], "COMPLETED")
            self.assertEqual(p.verify()["status"], "PASS")
            self.assertEqual(p.migrate(progress=lambda *a: None)["status"], "COMPLETED")   # idempotent rerun
            self.assertEqual(p.verify()["status"], "PASS")
        finally:
            p.rollback(st.target_schema)
