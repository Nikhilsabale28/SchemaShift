# Testing status (authoring sandbox) — what was and was not actually run

**Environment:** Python 3.12.3, Linux, **no network**, no MongoDB, no PostgreSQL. Installed packages: only the standard
library (plus scikit-learn, unused). `pytest`, `fastapi`, `httpx`, `pymongo`, `SQLAlchemy`, `psycopg`, `rapidfuzz`,
`langgraph`, `openai` were **not installed** and could not be installed.

## Command run
```
python -m unittest discover -s tests -t . -v
```
## Result: `Ran 95 tests ... OK (skipped=10)`  →  **85 passed, 0 failed, 10 skipped**

| Area | Tests run and passed |
|---|---|
| Name normalization, type compatibility, deterministic scoring, caps, aggregation | yes |
| Inspection (sampling, nullable/absent), inference (nested flatten, array→child table, FK resolution) | yes |
| Similarity report, unmatched fields, review gates (threshold, mixed types, unresolved FK) | yes |
| Schema-map lifecycle: accept/reject/override, freeze blocking, hash tamper detection, revisions, invalidation | yes |
| DDL generation: ordering, cycles, identifier safety, reproducibility, rollback scope | yes |
| ETL transforms: ObjectId, dates/timezones, NUMERIC precision, nulls, nested objects, arrays, unmapped fields, required fields | yes |
| Migration runner (against the in-memory sink): success, idempotent rerun, precondition failures, transform failure writes nothing, load failure rolls back | yes |
| Verification: PASS, missing row, wrong value, orphan FK, wrong type, NULL in required column, unfrozen map | yes |
| Pipeline artifacts: reproducibility, invalidation after new revision, modified-file detection, frozen map not replaced, rollback guards | yes |
| Mock AI provider, defensive JSON parsing, scrubbing, OpenAI→mock fallback, configuration validation, secret redaction | yes |
| Workflow node logic + routing (`run_sequential`): success, rejection, review rounds, failed migration, failed verification, AI failure tolerance | yes |
| ORM generation: valid Python, classes/relationships/constraints, determinism, change report | yes |

## Skipped (10) — NOT run, so NOT verified
* 5× `TestAPI` (FastAPI `TestClient`) — fastapi/httpx missing.
* 3× `TestLangGraph` (real interrupt/resume graph) — langgraph missing.
* 1× `TestORMCrudSQLite` (generated models CRUD on SQLite) — SQLAlchemy missing.
* 1× `test_integration_db` (real MongoDB + PostgreSQL end to end) — needs databases and `SCHEMASHIFT_INTEGRATION=1`.

## Written but never executed here
`app/main.py` + `app/api/routes.py` (only byte-compiled), `app/workflow/graph.py` (LangGraph wiring; only byte-compiled),
`app/migration/sink.py::PostgresSink`, `app/inspection/source.py::MongoSource`, `app/ai/openai_provider.py`,
`app/orm/repository.py`, `scripts/crud_demo.py`, `sample_data/generate.py::seed_mongo`, Dockerfile, docker-compose.yml.
Generated `schema.sql` was reviewed by reading it, not executed on PostgreSQL; generated `orm_models.py` was only parsed/compiled.
Not run: `pytest` itself, Python 3.11 (grammar-only check via `ast`), the packaged ZIP's dependencies install.
Dependency version ranges in `requirements.txt` are unverified.

Please run on your machine: `pip install -r requirements.txt`, `python -m pytest`, then the integration test (see RUN_IN_VSCODE.md §17).
Report any failure — the first real-database run is where an untested path is most likely to show a bug.
