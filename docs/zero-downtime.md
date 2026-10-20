# Zero-downtime migration — DESIGN ONLY (not implemented)

SchemaShift today performs a **offline, one-shot** migration with verification. Nothing in this document is implemented
or tested, and SchemaShift does **not** claim zero downtime. Deterministic code would have to own every write, cutover
and rollback decision; AI could only advise on readiness and risk.

## Expand / contract
1. **Expand** – create the PostgreSQL schema next to MongoDB (already done by `schema.sql`). The application keeps using MongoDB.
2. **Backfill** – bulk-load a consistent snapshot (SchemaShift's migration, ideally from a Mongo snapshot / `clusterTime`).
3. **Catch up** – apply changes made after the snapshot:
   * *Dual-write*: the application writes to MongoDB (source of truth) and PostgreSQL through the generated ORM; failures of the second write go to a retry queue. Risk: partial failures create divergence.
   * *Change-data-capture*: tail MongoDB change streams (resume tokens persisted) and apply each event through the same frozen-map transform. Risk: ordering, deletes, schema drift in new documents (they must fail loudly into a dead-letter queue, never be dropped).
4. **Shadow reads** – serve from MongoDB, also read PostgreSQL and compare in the background; metrics on mismatch rate.
5. **Reconcile / verify** – run the deterministic verification continuously on a sliding window and fully before cutover.
6. **Cutover** – flip reads (feature flag) to PostgreSQL, keep dual-writing; then flip writes. Requires a freeze window or write-fence per entity to avoid lost updates.
7. **Rollback** – while dual-writing continues, switch flags back; after writes moved, rollback needs reverse sync (PostgreSQL → MongoDB) which is **not available**.
8. **Decommission** – stop dual-write, keep a read-only MongoDB backup for an agreed period, then retire.
**Contract** = remove the old access path only after the retention period.

## Failure scenarios and consistency risks
Lost or reordered change events; dual-write partial failure; new document shapes with unmapped fields (map must be re-approved: new revision → freeze → regenerate); clock skew on timestamps; long transactions blocking backfill; cutover while lag > 0.
Readiness gates (all deterministic): lag below threshold, zero verification mismatches for N windows, empty dead-letter queue, artifact manifest current.
