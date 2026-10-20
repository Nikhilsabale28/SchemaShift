# Design notes
* **Why a frozen hash?** `content_hash` binds generated SQL/ORM/ETL plan and the migration run to one reviewed map; any edit is detectable.
* **Why transform-then-load?** Finding all bad documents before writing anything makes failures cheap and the target clean.
* **Why `position` on child tables?** Array elements have no identity in MongoDB. `(parent id, index)` is a deterministic natural key enabling idempotent re-runs and exact verification.
* **Why in-memory sinks?** `InMemorySource/InMemorySink` implement the same contract (PK/UNIQUE conflict policy, FK and NOT NULL enforcement, transactions) as the PostgreSQL sink so the whole pipeline is unit-testable without databases. They are not a substitute for the PostgreSQL integration test.
* **Weights.** 50/30/20 as specified; two documented adjustments: caps at 0.60 for unsafe types / unresolved FKs, and structural keys scored 1.0.
