# SchemaShift 1.0.0

AI-assisted **MongoDB → PostgreSQL** migration tool. It inspects MongoDB, proposes a relational schema, scores the
match with deterministic code, forces a human to approve uncertain mappings, **freezes** the approved map (SHA-256),
generates SQL / ETL plan / rollback / SQLAlchemy models **only from the frozen map**, migrates the data, and verifies
the result with code that outputs PASS or FAIL. AI (optional) only suggests and explains.

> New here? Read [`RUN_IN_VSCODE.md`](RUN_IN_VSCODE.md) (Windows + VS Code + PowerShell, step by step).
> No databases yet? `python -m app.cli demo --memory --yes` runs the whole pipeline in memory.

## 1. Problem statement
Teams outgrow MongoDB's flexible documents and want PostgreSQL's constraints and joins. A manual migration means
understanding implicit structure, turning nested documents into tables, mapping types, moving data in the right order,
proving nothing was lost, and updating the data-access layer. SchemaShift automates the mechanical parts and keeps a
human in charge of the uncertain decisions.

## 2. Pipeline
```
seed -> inspect -> infer schema -> similarity -> [human review] -> freeze (hash) -> generate -> migrate -> verify -> ORM CRUD
```
| Step | CLI command | Output |
|---|---|---|
| Inspect | `python -m app.cli inspect` | `artifacts/inspection_report.json` |
| Infer + score + draft map | `python -m app.cli infer` | `artifacts/schema_proposal.json`, `artifacts/schema_map.json` (draft), `reports/similarity_report.{json,md}` |
| Review | `python -m app.cli review` | updates the draft map |
| Freeze | `python -m app.cli freeze` | map becomes `frozen` + `content_hash` |
| Generate | `python -m app.cli generate` | `migrations/generated/{schema.sql,rollback.sql,orm_models.py,etl_plan.json,change_report.md,manifest.json}` |
| Migrate | `python -m app.cli migrate` | `reports/migration_report.json` |
| Verify | `python -m app.cli verify` | `reports/verification_report.json` (PASS/FAIL) |
| CRUD demo | `python -m app.cli crud` | creates/reads/updates/deletes through the generated ORM on PostgreSQL |
| Everything | `python -m app.cli demo` or `... workflow` | all stages in order, approval gate kept |

Real example outputs (produced by running the in-memory demo) are in [`docs/examples/`](docs/examples/).

## 3. Hybrid design: deterministic core, AI as assistant

| Layer | Deterministic component | AI-assisted (non-deterministic) component | Why this split | Safety gate | Match-percentage handling |
|---|---|---|---|---|---|
| Inspect / infer | Seeded sampling, field/type frequencies, null/absent detection, nested-object and array rules, reference detection (`customerId` → `customers`) | `suggest_mappings` is available on the provider interface but is not invoked by the workflow | Observed structure is a fact and must be reproducible; naming/design advice is opinion | Human approval + freeze before anything is generated | Per-column scores computed during inference; AI never edits them |
| Similarity / match | Name tokens + RapidFuzz, type-compat table, structure rules, caps, aggregation formula (below) | Plain-language explanations of weak matches (`explain_field`) | A percentage must be recomputable and auditable | Score < threshold, mixed types or unresolved FK ⇒ mandatory review | 100 % code. AI text is labelled advisory and cannot change a number |
| Script generation | DDL, ETL plan, rollback, ORM from the frozen map; identifier validation/quoting; allow-listed SQL types; dependency order + cycle detection | None; risk summaries run during review, not generation | Executable artifacts must be identical for identical input and never contain model-written SQL | Hash-verified frozen map required; manifest invalidates artifacts when the map changes | Not used; map confidence scores are copied from the report |
| Data migration | Strict conversions, unmapped-field detection, parents-first loading, batches, one transaction, idempotent re-runs, failure report | Diagnose failures and suggest mapping corrections (`diagnose_failure`) | Writes must be exact, atomic and explainable | Needs current artifacts; any mapping fix ⇒ new revision ⇒ re-approve ⇒ re-freeze | Not used |
| Verification / rollback | Count, value, checksum, FK, NOT NULL and type checks; PASS only if all pass; rollback script limited to demo schema | None | An LLM must never turn FAIL into PASS | Failed verification ⇒ workflow status FAILED; rollback needs `ALLOW_DESTRUCTIVE_CLEANUP=true` + schema-name confirmation | Not used |
| ORM migration | SQLAlchemy 2.x models, relationships, repositories generated from the frozen map | None | Generated code must match the schema exactly | Generated models are tied to the map hash; CRUD demo exercises them | Not used |
| Orchestration | LangGraph state machine, routing, error short-circuit, `interrupt()` approval gate | AI notes attached to the state (`ai_notes`) | Control flow and side effects must be predictable | Human approval before freeze; migration only after freeze + generation; error ⇒ FAILED | Overall % stored in state from code |

**AI can never**: set a score, change a frozen map, execute SQL/Python, skip approval, override a failed verification,
or touch the database. Only schema metadata and *scrubbed* error patterns (quoted values removed) are ever sent to
OpenAI — never documents. `AI_PROVIDER=mock` (default) works offline and is a deterministic rule engine, **not an LLM**.

In the current workflow, `explain_field` and `summarize_risks` supply optional advisory notes during review, while
`diagnose_failure` may annotate a failed migration. `suggest_mappings` is available on the provider interface but
is not currently invoked by the workflow. Inspection, inference, similarity scores, approval, map freezing, artifact
generation, migration, and verification remain deterministic; provider failures are reported as failures and never
silently replaced with mock output.

## 4. Similarity algorithm
Names are normalised (camelCase/PascalCase split, lower-cased, `_ - space` unified, abbreviation dictionary such as
`qty→quantity`, `zip→postal`). Then for every source field → target column:

```
score = 0.50 * name  +  0.30 * type  +  0.20 * structure          (weights in app/matching/similarity.py: Weights)
name      = 1.0 if sorted tokens are equal, else RapidFuzz ratio of the sorted token strings (difflib fallback)
type      = compatibility table in app/core/types.py (1.0 lossless … 0.0 incompatible)
structure = 0.6 if collection↔table (or array-child) match  +  0.4 if last path segment / flattened path ↔ column
Caps:  type compat < 0.5  -> score <= 0.60      unresolved foreign key -> score <= 0.60
status: matched >= 0.90, guessed >= 0.50, else unmatched
```
**Aggregation**: table score = mean of its field scores, where observed source fields that were not mapped count as
**0**; overall = (sum of all field scores) / (number of all fields). Missing data therefore always lowers the
percentage. Everything is recomputable from `inspection_report.json` + `schema_proposal.json`.

**Review is mandatory** (regardless of the score) for: score < `SIMILARITY_THRESHOLD` (default 0.75), inconsistent
source types, unresolved foreign keys. Structurally derived keys (parent key, array position, generated id) are not
name-matched: they score 1.0 with an explanatory note. Note that for the demo data nothing falls below the
threshold, so the review step lists zero items; the freeze step still needs an explicit `APPROVE` (set
`SIMILARITY_THRESHOLD=0.9999` to see the review loop).

Example (from `docs/examples/similarity_report.md`): overall 99.4 %, customers/orders/products 100 %, orders_items 96.25 %.

## 5. Transformation strategy
* `_id` → `id VARCHAR(24)` primary key; any ObjectId → 24-char hex text.
* Nested **scalar** object → flattened columns (`address.postalCode` → `address_postal_code`).
* **Array of objects** → child table `<parent>_<field>` (`orders_items`) with FK to the parent, a generated `BIGSERIAL id`,
  and `position` (array index) with `UNIQUE (order_id, position)` — this makes re-runs idempotent.
* Array of scalars → child table with a `value` column.
* `customerId`-style ObjectId fields whose plural collection exists → foreign key; otherwise **unresolved → review**.
* Mixed types → `TEXT` + review (never silently coerced); numeric-only mixes widen to DOUBLE/NUMERIC.
* Optional fields (absent/null anywhere, also per array element) → nullable columns; everything else `NOT NULL`.
* Abbreviation expansion can rename (`qty` → `quantity`); `source_path` always records the original path.
* Naive datetimes are interpreted as UTC. NUMERIC conversion fails instead of rounding. Unknown fields **fail the
  migration** (they are never dropped).

## 6. Schema map, approval and freezing
`artifacts/schema_map.json` holds, per table/column: source collection & path, target name/type, nullability, transform
rule, confidence, review status, reasons, rationale, FKs, unique keys; plus `revision`, `status`, approval metadata,
creation metadata and `content_hash` (SHA-256 of canonical content).
* `freeze` is refused while any mapping is `needs_review` or `rejected`.
* A frozen map cannot be edited (`SchemaMapError`); editing it creates **revision N+1 as a draft** and deletes all generated artifacts.
* Every consumer calls `require_frozen()`, which recomputes the hash → a tampered file is rejected.
* `generated/manifest.json` records the map hash and a SHA-256 per generated file; `migrate` refuses stale or hand-edited artifacts.
* `infer` never overwrites a frozen map unless `--force` is given. AI suggestions are never applied automatically.

## 7. Migration policy, recovery and rollback
1. Preconditions: frozen + hash-valid map, current artifacts, source and target reachable, source collections present.
2. **Phase 1** transforms *every* document first. Any error ⇒ status `FAILED_TRANSFORM`, **nothing written**, report lists collection/document id/path/message.
3. **Phase 2** loads parents first in batches inside **one PostgreSQL transaction**; any error ⇒ everything rolled back (`FAILED_LOAD`).
4. **Re-runs**: `INSERT … ON CONFLICT DO NOTHING` on the primary key (child tables: `(parent_id, position)`); skipped rows are counted. Verification compares values, so a stale different row is still caught.
5. Rollback (`python -m app.cli rollback --confirm-schema schemashift_demo`) drops **only** the tables named in the frozen map inside the configured schema, only if `ALLOW_DESTRUCTIVE_CLEANUP=true`. It is **irreversible**; it is not exposed over HTTP; it never drops databases or schemas.
6. Limitations: all transformed rows are held in memory (fine for demo scale, not for huge collections); no CDC, so changes made in MongoDB during migration are not captured (see `docs/zero-downtime.md`).

## 8. Verification
Checks (all computed in code, see `app/verification/verify.py`): source documents transform cleanly; per table — row counts,
value types, NOT NULL, rows present/no extras, critical fields (`email`, `price`, `unit_price`, `quantity`, `status`,
`order_date`, `customer_id`, `product_id`), full-table SHA-256 over canonicalised rows; plus FK integrity per foreign key.
`PASS` only if **every** check passes. The report lists what cannot be compared exactly (generated child ids, converted
ids/timestamps, nested documents split across tables). A FAIL carries recovery guidance; the workflow ends `FAILED`.

## 9. LangGraph workflow
`app/workflow/graph.py` — nodes: `inspect_source → infer_schema → calculate_similarity → mismatch_report → human_approval → freeze_map → generate_artifacts → migrate_data → verify_data → final_report`.
* `human_approval` calls LangGraph `interrupt(payload)`; the caller resumes with `Command(resume={"approve": true, "approved_by": "...", "decisions": [...]})`. If mappings are still pending the node loops back to approval (max 20 rounds); `approve: false` ⇒ `REJECTED`.
* Any node error short-circuits to `final_report`; status is `SUCCESS` only if verification is `PASS`.
* State persistence: in-memory `MemorySaver` by default; set `CHECKPOINT_DB=checkpoints.sqlite` to use `SqliteSaver` so an interrupted run can be resumed with the same `--thread-id` from a new process.
* Run: `python -m app.cli workflow` (add `--memory --yes` for a database-free smoke run).
* Node logic and routing live in `app/workflow/nodes.py` and are unit-tested without LangGraph; the graph wiring itself needs `langgraph` installed to be tested.

## 10. REST API (`uvicorn app.main:app --reload`, Swagger at `/docs`)
`GET /health[?deep=true]`, `GET /api/v1/source/collections`, `POST /api/v1/inspection/run`, `GET /api/v1/inspection/report`,
`POST /api/v1/schema/infer`, `GET /api/v1/schema-map`, `GET /api/v1/similarity/report?format=json|markdown`,
`POST /api/v1/schema/approve`, `POST /api/v1/schema/freeze`, `POST /api/v1/artifacts/generate`, `GET /api/v1/artifacts/status`,
`POST /api/v1/migration/run` (**202**, background thread), `GET /api/v1/migration/status` (`running|completed|failed`),
`POST /api/v1/verification/run`, `GET /api/v1/verification/report`, `GET /api/v1/orm/models`, `POST /api/v1/ai/explain`, `POST /api/v1/ai/diagnose`.
Errors: 404 missing artifact, 409 gate violated (not frozen, pending reviews, stale artifacts, job running), 422 invalid body, 503 database unreachable. No rollback/cleanup endpoint exists.

## 11. ORM migration
`migrations/generated/orm_models.py` (generated, never edited) holds SQLAlchemy 2.x `DeclarativeBase` models with
relationships (`Customer.orders`, `Order.customer`, `Order.orders_items`, `OrderItem.product`, …). Handwritten code lives in
`app/orm/repository.py` (generic `Repository` CRUD + `OrderService`) and `scripts/crud_demo.py`. `change_report.md` documents
Mongo-path → table/column mappings and access-pattern changes (embedded array → join).

## 12. Configuration & security
All settings come from `.env` / environment (`.env.example`; no secrets in the repo or ZIP). Identifiers are validated
(`^[a-z_][a-z0-9_]{0,62}$`) and quoted; column types are allow-listed; data values go through bound parameters; no
LLM-written SQL/Python is ever executed; logs redact passwords in URIs and `sk-…` keys; connection timeouts are set;
destructive cleanup is off by default. Configuration uses a small stdlib loader (not `pydantic-settings`) so the core runs
without extra packages; Pydantic is used for API models.

## 13. Folder structure
```
app/            config.py, cli.py, main.py, pipeline.py (shared service)
  core/ matching/ inspection/ inference/ schema_map/ generation/ etl/ migration/ verification/ orm/ ai/ workflow/ api/ schemas/ utils/
sample_data/    generate.py (10 customers / 8 products / 20 orders, deterministic)
scripts/        crud_demo.py
migrations/generated/   generated artifacts (git-ignored)      artifacts/ reports/   run outputs
docs/           zero-downtime.md, design-notes.md, examples/   tests/   unit + (skipped-by-default) integration tests
```
(`app/approvals`, `app/database` are intentionally empty packages kept for the requested layout; approval logic lives in `schema_map` and `pipeline`.)

## 14. Testing — what was actually run
```powershell
# Install dependencies first
pip install -r requirements.txt

# Full unit test suite (115 passed, 1 skipped — integration test)
python -m pytest -q

# AI provider and workflow tests only
python -m pytest tests/test_ai.py tests/test_workflow.py -v

# Integration tests (requires MongoDB + PostgreSQL + configured .env)
$env:SCHEMASHIFT_INTEGRATION="1"; python -m pytest tests/test_integration_db.py -v

# Opt-in live OpenAI smoke test (one request; requires OPENAI_API_KEY in .env)
$env:AI_PROVIDER = "openai"
$env:SCHEMASHIFT_OPENAI_SMOKE = "1"
python -m scripts.openai_smoke_test
$env:SCHEMASHIFT_OPENAI_SMOKE = "0"
```

Verified on Python 3.11.9 (Windows): **115 passed, 1 skipped** (integration test skipped — needs real databases).
All unit tests, API tests, ORM tests, workflow tests, and AI provider tests pass offline.

The 1 skipped test (`test_integration_db.py::TestRealDatabases`) requires `SCHEMASHIFT_INTEGRATION=1` plus live MongoDB and PostgreSQL.

## 15. OpenAI provider setup (Windows PowerShell)

The default `AI_PROVIDER=mock` works offline without any API key. To use real OpenAI:

```powershell
# 1. Copy the example env file (only if you haven't already)
Copy-Item .env.example .env

# 2. Open the local file in your editor; never paste the key in a command or chat.
code .env
# Set these values in the editor:
#    AI_PROVIDER=openai
#    OPENAI_API_KEY=<your key>   <- enter your own key; never paste it in chat or source code
#    OPENAI_MODEL=gpt-4o-mini    <- or any model your account can access
#    OPENAI_TIMEOUT=30

# 3. Verify provider selection (key is redacted in output)
python -m app.cli info

# 4. Run the workflow with real LLM advisory notes
python -m app.cli workflow --memory --yes

# 5. Optional: authorize exactly one harmless live Responses API request
$env:SCHEMASHIFT_OPENAI_SMOKE = "1"
python -m scripts.openai_smoke_test
$env:SCHEMASHIFT_OPENAI_SMOKE = "0"

# 6. Return to offline mock mode in this PowerShell session
$env:AI_PROVIDER = "mock"
python -m app.cli info
```

**Requirements:**
- Install the pinned range declared in `requirements.txt` with `python -m pip install -r requirements.txt`.
  The OpenAI SDK requirement is `openai>=1.66,<3.0`; the Responses API (`client.responses.create`) was introduced
  in this version range.
- An OpenAI account with billing enabled and available API credits.
- The model `gpt-4o-mini` is used by default; change `OPENAI_MODEL` if your account requires a different model.
- Keep the key only in the ignored local `.env` file. `python -m app.cli info` masks the key; the smoke test never
  prints it. Do not put it in shell history, source code, or shared logs.
- To opt in to the smoke test, set `SCHEMASHIFT_OPENAI_SMOKE=1`; without that exact value the script makes no request.
  The script reports PASS only after validating a JSON response. The ordinary test suite never contacts OpenAI.
- Missing credentials or an invalid provider selection are configuration errors; authentication, rate-limit,
  timeout, and connectivity failures are returned as explicit failed AI results, with safe diagnostic messages.
- Set `AI_PROVIDER=mock` to restore offline operation. Mock responses are clearly labelled deterministic mock output,
  not live OpenAI responses.

## 16. Limitations and future work
* Large-scale streaming migration is **not implemented** — all transformed rows are held in memory (fine for demo scale).
* Zero-downtime migration is a **documented design only** (`docs/zero-downtime.md`). No CDC is implemented.
* SQL→MongoDB reverse migration is **not implemented**; it is future work.
* Dependency versions are ranges, not a verified lock file.
* One source database per run; English abbreviation dictionary only.
