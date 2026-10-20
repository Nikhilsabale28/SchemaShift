# Running SchemaShift on Windows 10/11 with VS Code and PowerShell

Everything below is typed in the **VS Code terminal (PowerShell)** from the project folder. Commands that are the same
every time are shown in order; follow them top to bottom.

> **Fastest sanity check (no databases needed):** after step 4 run
> `python -m app.cli demo --memory --yes`. It runs the full pipeline in memory and should end with `verification: PASS`.

## 0. Basic vs optional
* **Basic local workflow** (needs MongoDB + PostgreSQL): steps 1–17. Works with `AI_PROVIDER=mock` and **no API key**.
* **Optional**: OpenAI provider (step 18), LangGraph workflow command (step 15), Docker for the databases (step 6b), FastAPI (step 19).

## 1. Install the software and check versions
| Software | Version | Download |
|---|---|---|
| Python | 3.11 or 3.12 (tick **"Add python.exe to PATH"** in the installer) | python.org |
| VS Code + "Python" extension | current | code.visualstudio.com |
| MongoDB Community Server | 7.x or 8.x (install as a Windows Service) | mongodb.com/try/download/community |
| PostgreSQL | 16 (remember the **password you set** for user `postgres`) | postgresql.org/download/windows |

Check in PowerShell:
```powershell
python --version          # Python 3.11.x or 3.12.x
pip --version
Get-Service MongoDB       # Status should be Running
Get-Service postgresql*   # Status should be Running
```
If `python` is not recognised, see Troubleshooting.

## 2. Open the project
Extract `SchemaShift.zip` (right-click → Extract All). In VS Code: **File → Open Folder…** → choose the extracted
`SchemaShift` folder (the one containing `README.md`). Open the terminal: **Terminal → New Terminal** (it should be PowerShell and
start in that folder; check with `Get-Location`).

## 3. Create and activate a virtual environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
The prompt now starts with `(.venv)`. If you get *"running scripts is disabled on this system"*, allow scripts **for this
terminal only** (no system-wide change):
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```
Alternative without activation: prefix commands with `.\.venv\Scripts\python.exe` (e.g. `.\.venv\Scripts\python.exe -m app.cli info`).
In VS Code press **Ctrl+Shift+P → "Python: Select Interpreter"** and pick `.venv`.

## 4. Install dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Create `.env`
```powershell
Copy-Item .env.example .env
code .env
```
Set at least the PostgreSQL line, using the password you chose when installing PostgreSQL (you must know it; SchemaShift
cannot recover it):
```
POSTGRES_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/schemashift_target
```
Special characters in the password must be URL-encoded (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `#` → `%23`, `%` → `%25`).
Leave `AI_PROVIDER=mock` and `OPENAI_API_KEY` empty. Never share or commit `.env`.

## 6a. Start the databases (installed locally)
```powershell
Start-Service MongoDB            # PowerShell must be "Run as administrator" for Start-Service
Start-Service postgresql-x64-16  # exact service name: Get-Service postgresql*
```
Create the target database (use the password you set at installation when prompted). If `psql` is not found, add
`C:\Program Files\PostgreSQL\16\bin` to PATH or call it by full path:
```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE schemashift_target;"
```
## 6b. (Alternative) Databases with Docker Desktop
```powershell
$env:POSTGRES_PASSWORD="choose-a-password"
docker compose up -d
```
Then use the same password in `POSTGRES_URL`.

Check your configuration (secrets are masked):
```powershell
python -m app.cli info
```

## 7. Seed MongoDB (10 customers, 8 products, 20 orders)
```powershell
python -m app.cli seed
```
Expected: `Seeded MongoDB database 'schemashift_demo' (only the demo collections were reset): {'customers': 10, 'products': 8, 'orders': 20}`

## 8. Inspect
```powershell
python -m app.cli inspect
```
Expected (example): `customers: 10 documents, 12 field paths` … and `Wrote artifacts/inspection_report.json`.

## 9. Infer the schema and see the match percentages
```powershell
python -m app.cli infer
python -m app.cli similarity
```
Expected (example): `Proposed tables: customers, orders, orders_items, products`, `Overall match: 99.4%`, `0 mapping(s) need human review`.
Open `reports/similarity_report.md` in VS Code (right-click → Open Preview) to see per-table and per-field scores.

## 10. Review and approve
```powershell
python -m app.cli review --list      # what needs a human
python -m app.cli review             # interactive: [a]ccept / [r]eject / [t]ype override / [f]k override
python -m app.cli review --accept customers.phone orders.shipping_method   # non-interactive form
```
To *see* the review loop with the sample data, set `SIMILARITY_THRESHOLD=0.9999` in `.env` and re-run `infer --force`.

## 11. Freeze
```powershell
python -m app.cli freeze --by "your name"      # type APPROVE when asked
```
Freezing is refused while anything is pending or rejected. Expected: `Frozen revision 1, hash ef2023…`.
To change a frozen map later: `python -m app.cli infer --force`, review, freeze, then regenerate (old artifacts are deleted).

## 12. Generate SQL, ETL plan, rollback and ORM files
```powershell
python -m app.cli generate
```
Inspect `migrations/generated/`: `schema.sql`, `rollback.sql`, `etl_plan.json`, `orm_models.py`, `change_report.md`, `manifest.json`.

## 13. Run the migration, then verify
```powershell
python -m app.cli migrate
python -m app.cli verify
```
Expected: `Migration status: COMPLETED` with counts `customers 10, products 8, orders 20, orders_items 39 (about)`, and
`VERIFICATION: PASS`. Running `migrate` again is safe (existing rows are skipped and reported).

## 14. CRUD with the generated ORM (PostgreSQL only)
```powershell
python -m app.cli crud
```
Prints CREATE / READ / UPDATE / DELETE lines and the first orders with their line items.

## 15. One-command demos
```powershell
python -m app.cli demo                    # real MongoDB + PostgreSQL, asks for approval
python -m app.cli workflow                # LangGraph workflow with a human-approval interrupt
python -m app.cli demo --memory --yes     # no databases at all
```

## 16. Rollback (optional, destructive for the demo schema only)
Edit `.env`: `ALLOW_DESTRUCTIVE_CLEANUP=true`, then:
```powershell
python -m app.cli rollback --confirm-schema schemashift_demo
```
It drops only the four demo tables of the frozen map in that schema. This cannot be undone.

## 17. Tests
```powershell
python -m pytest
```
Tests that need FastAPI/LangGraph/SQLAlchemy run once requirements are installed. Database integration test (uses your `.env`, reseeds the demo collections):
```powershell
$env:SCHEMASHIFT_INTEGRATION="1"; python -m pytest tests/test_integration_db.py -v
```

## 18. Optional: OpenAI instead of mock
In `.env`: `AI_PROVIDER=openai`, `OPENAI_API_KEY=...`, `OPENAI_MODEL=...`. Only schema metadata and scrubbed error patterns are sent.
If the key is missing or the call fails, SchemaShift prints a notice and falls back to the mock provider; the pipeline is unaffected.

## 19. Start the API and open Swagger
```powershell
uvicorn app.main:app --reload
```
Open <http://127.0.0.1:8000/docs>. Typical order: `POST /api/v1/inspection/run` → `POST /api/v1/schema/infer` →
`POST /api/v1/schema/approve` (if needed) → `POST /api/v1/schema/freeze` → `POST /api/v1/artifacts/generate` →
`POST /api/v1/migration/run` (returns 202) → poll `GET /api/v1/migration/status` → `POST /api/v1/verification/run`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `python` is not recognized | Re-run the Python installer → *Modify* → tick "Add to PATH", or use `py -3.12 -m venv .venv`. Reopen the terminal. |
| Wrong Python version | `py -0` lists versions; create the venv with `py -3.12 -m venv .venv` (3.11/3.12 supported). Delete `.venv` first if it exists. |
| `running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` (this terminal only). |
| `pip install` fails / build errors | Upgrade pip (`python -m pip install --upgrade pip`); check internet/proxy; make sure Python is 64-bit 3.11/3.12. Install one package at a time to find the culprit. |
| `ModuleNotFoundError: No module named 'app'` | You are not in the project root. `cd` to the folder with `README.md`. |
| `cannot reach MongoDB` | `Get-Service MongoDB`; `Start-Service MongoDB` (admin PowerShell); check `MONGO_URI` and that port 27017 is free (`netstat -ano | findstr 27017`). |
| `password authentication failed` | The password in `POSTGRES_URL` differs from the one set at install. Reset it in psql/pgAdmin, or fix `.env`. URL-encode special characters. |
| `database "schemashift_target" does not exist` | Run the `CREATE DATABASE` command from step 6a. |
| `connection refused` on 5432 | Start the PostgreSQL service; if another program uses 5432, change the port in `POSTGRES_URL`. |
| `relation ... does not exist` / missing tables | You skipped `generate`/`migrate`, or ran `rollback`. Run `generate` then `migrate`. |
| `POSTGRES_URL still contains the placeholder password` | Edit `.env` (step 5). |
| `ConfigError: SIMILARITY_THRESHOLD must be ...` | Use a value like `0.75` (or `75`). Remove stray quotes/spaces in `.env`. |
| Port 8000 busy | `uvicorn app.main:app --port 8001`. |
| `AI_PROVIDER=openai unavailable (OPENAI_API_KEY is not set)` | Expected without a key: the mock provider is used. Add a key or switch back to `mock`. |
| `FAILED_TRANSFORM` | Open `reports/migration_report.json` → `errors[]` shows collection, document id, path, message. Fix the data or start a new map revision (override type / map the field), re-approve, freeze, generate, migrate. |
| `VERIFICATION: FAIL` | Read the failing checks and `mismatches[]` in `reports/verification_report.json`. Do not use the data. Fix the cause, then rollback (step 16) and re-run, or revise the map. |
| `generated file ... was modified` | Artifacts must match the frozen map: run `python -m app.cli generate` again. |
