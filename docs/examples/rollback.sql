-- SchemaShift rollback for schema schemashift_demo: drops only the tables listed below.
-- IRREVERSIBLE: data in these tables is lost. Run via scripts/rollback (requires ALLOW_DESTRUCTIVE_CLEANUP=true).
DROP TABLE IF EXISTS "schemashift_demo"."orders_items";
DROP TABLE IF EXISTS "schemashift_demo"."orders";
DROP TABLE IF EXISTS "schemashift_demo"."products";
DROP TABLE IF EXISTS "schemashift_demo"."customers";
