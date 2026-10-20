"""Pipeline service: ONE place that wires inspection -> inference -> scoring -> approval -> freeze ->
generation -> migration -> verification. Used by the CLI, the FastAPI app and the LangGraph workflow."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from .config import Settings
from .utils.store import Store
from .inspection.inspector import inspect_database
from .inference.infer import infer_schema
from .matching.report import build_similarity_report, to_markdown
from .matching.similarity import Weights
from .schema_map import schema_map as sm_mod
from .schema_map.schema_map import SchemaMapError
from .generation.ddl import generate_ddl, generate_rollback
from .orm.generate import generate_models, change_report
from .etl.transform import insert_columns, conflict_columns
from .migration.runner import run_migration
from .verification.verify import verify_migration

GENERATED_FILES = ("schema.sql", "rollback.sql", "orm_models.py", "change_report.md", "etl_plan.json")

class PipelineError(Exception):
    pass

def make_source(st: Settings):
    from .inspection.source import MongoSource
    return MongoSource(st.mongo_uri, st.mongo_db, st.mongo_collections)

def make_sink(st: Settings):
    from .migration.sink import PostgresSink
    st.require_postgres_configured()
    return PostgresSink(st.postgres_url, st.target_schema)

def _sha(text: str) -> str: return hashlib.sha256(text.encode()).hexdigest()

class Pipeline:
    def __init__(self, settings: Settings, source=None, sink=None):
        self.st, self._source, self._sink = settings, source, sink
        self.store = Store(settings.base_dir)

    @property
    def source(self):
        if self._source is None: self._source = make_source(self.st)
        return self._source
    @property
    def sink(self):
        if self._sink is None: self._sink = make_sink(self.st)
        return self._sink

    # ---- inspection / inference / similarity
    def source_collections(self): return self.source.collection_names()

    def run_inspection(self) -> dict:
        self.source.ping()
        names = self.source.collection_names()
        rep = inspect_database({n: self.source.documents(n) for n in names}, self.st.sample_size, self.st.sample_seed)
        rep["source_database"] = getattr(self.source, "database", self.st.mongo_db)
        Store.write_json(self.store.inspection, rep)
        return rep

    def infer(self) -> dict:
        proposal = infer_schema(Store.read_json(self.store.inspection))
        Store.write_json(self.store.proposal, proposal)
        return proposal

    def similarity(self) -> dict:
        rep = build_similarity_report(Store.read_json(self.store.inspection), Store.read_json(self.store.proposal),
                                      weights=Weights(), review_threshold=self.st.similarity_threshold)
        Store.write_json(self.store.similarity_json, rep)
        self.store.similarity_md.write_text(to_markdown(rep), encoding="utf-8")
        return rep

    # ---- schema map
    def create_draft_map(self, force: bool = False) -> dict:
        if self.store.exists(self.store.schema_map):
            old = Store.read_json(self.store.schema_map)
            if old.get("status") == "frozen" and not force:
                raise PipelineError("a frozen schema map exists; it is never replaced automatically. "
                                    "Use begin_revision() to edit it, or force=True to discard it.")
            self.invalidate_artifacts()
        insp = Store.read_json(self.store.inspection)
        sm = sm_mod.create_schema_map(Store.read_json(self.store.proposal), Store.read_json(self.store.similarity_json),
                                      insp.get("source_database", self.st.mongo_db), self.st.similarity_threshold)
        self.save_map(sm); return sm

    def load_map(self) -> dict: return Store.read_json(self.store.schema_map)
    def save_map(self, sm): Store.write_json(self.store.schema_map, sm)

    def apply_decisions(self, decisions: list) -> dict:
        sm = self.load_map()
        for d in decisions:
            sm_mod.apply_decision(sm, d["table"], d["column"], d["action"], sql_type=d.get("sql_type"),
                                  new_name=d.get("new_name"), ref_table=d.get("ref_table"),
                                  ref_column=d.get("ref_column", "id"), by=d.get("by", "reviewer"))
        self.save_map(sm); return sm

    def freeze(self, approved_by: str) -> dict:
        sm = self.load_map()
        sm_mod.freeze(sm, approved_by)
        self.save_map(sm); return sm

    def begin_revision(self) -> dict:
        """Edit a frozen map: creates revision N+1 as a DRAFT and invalidates all generated artifacts."""
        new = sm_mod.start_new_revision(self.load_map())
        self.invalidate_artifacts(); self.save_map(new); return new

    # ---- generation
    def invalidate_artifacts(self):
        for n in GENERATED_FILES + ("manifest.json",):
            p = self.store.generated / n
            if p.exists(): p.unlink()

    def generate_artifacts(self) -> dict:
        sm = self.load_map()
        h = sm_mod.require_frozen(sm)
        schema = self.st.target_schema
        plan = {"map_hash": h, "tables": {
            t: {"source_collection": s["source_collection"], "kind": s["kind"], "array_path": s["array_path"],
                "insert_columns": insert_columns(s), "conflict_columns": conflict_columns(s),
                "columns": {c: {k: v[k] for k in ("source_path", "sql_type", "transform", "nullable")}
                            for c, v in sorted(s["columns"].items())}}
            for t, s in sorted(sm["tables"].items())}}
        files = {"schema.sql": generate_ddl(sm, schema), "rollback.sql": generate_rollback(sm, schema),
                 "orm_models.py": generate_models(sm, schema), "change_report.md": change_report(sm),
                 "etl_plan.json": json.dumps(plan, indent=2, sort_keys=True) + "\n"}
        for n, text in files.items():
            (self.store.generated / n).write_text(text, encoding="utf-8", newline="\n")
        manifest = {"map_hash": h, "map_revision": sm["revision"], "target_schema": schema,
                    "files": {n: _sha(t) for n, t in sorted(files.items())}}
        Store.write_json(self.store.generated / "manifest.json", manifest)
        return manifest

    def artifacts_status(self) -> tuple:
        """(is_current, reason). Artifacts are current only for the exact frozen-map hash and unmodified files."""
        try:
            h = sm_mod.require_frozen(self.load_map())
        except (SchemaMapError, FileNotFoundError) as e:
            return False, str(e)
        mf = self.store.generated / "manifest.json"
        if not mf.is_file(): return False, "artifacts have not been generated for the frozen map"
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m["map_hash"] != h: return False, "artifacts were generated from a different schema map; regenerate"
        for n, digest in m["files"].items():
            p = self.store.generated / n
            if not p.is_file() or _sha(p.read_text(encoding="utf-8")) != digest:
                return False, f"generated file {n} is missing or was modified; regenerate"
        return True, "ok"

    def _require_artifacts(self):
        ok, why = self.artifacts_status()
        if not ok: raise PipelineError(why)

    # ---- migration / verification / rollback
    def migrate(self, progress=print) -> dict:
        self._require_artifacts()
        sm = self.load_map()
        ddl = (self.store.generated / "schema.sql").read_text(encoding="utf-8")
        rep = run_migration(sm, self.source, self.sink, ddl, batch_size=self.st.batch_size, progress=progress)
        Store.write_json(self.store.migration_report, rep); return rep

    def verify(self) -> dict:
        sm = self.load_map(); sm_mod.require_frozen(sm)
        rep = verify_migration(sm, self.source, self.sink)
        Store.write_json(self.store.verification_report, rep); return rep

    def rollback(self, confirm_schema: str) -> str:
        if not self.st.allow_destructive_cleanup:
            raise PipelineError("destructive cleanup is disabled: set ALLOW_DESTRUCTIVE_CLEANUP=true in .env to enable")
        if confirm_schema != self.st.target_schema:
            raise PipelineError(f"confirmation mismatch: pass the configured demo schema name '{self.st.target_schema}'")
        self._require_artifacts()
        self.sink.execute_script((self.store.generated / "rollback.sql").read_text(encoding="utf-8"))
        return f"dropped the tables of schema map {self.load_map()['content_hash'][:12]} in schema {self.st.target_schema}"

    # ---- convenience for summaries
    def schema_summary(self) -> dict:
        sm = self.load_map()
        return {"tables": {t: {"kind": s["kind"], "columns": [
            {"name": c, "sql_type": v["sql_type"], "mixed_types": v["mixed_types"], "unresolved_fk": v.get("unresolved_fk", False)}
            for c, v in sorted(s["columns"].items())]} for t, s in sorted(sm["tables"].items())}}
