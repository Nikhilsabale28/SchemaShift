"""SchemaShift command line. Run `python -m app.cli --help`. Typical order:
seed -> inspect -> infer -> review -> freeze -> generate -> migrate -> verify -> crud   (or: demo)"""
from __future__ import annotations
import argparse, dataclasses, sys
from pathlib import Path
from .config import load_settings, ConfigError
from .pipeline import Pipeline, PipelineError
from .schema_map.schema_map import SchemaMapError, pending_reviews
from .utils.store import Store

def _p(args=None) -> Pipeline:
    return Pipeline(load_settings())

def cmd_seed(a):
    from sample_data.generate import seed_mongo
    st = load_settings()
    counts = seed_mongo(st.mongo_uri, st.mongo_db)
    print(f"Seeded MongoDB database '{st.mongo_db}' (only the demo collections were reset): {counts}")

def cmd_inspect(a):
    rep = _p().run_inspection()
    for n, c in rep["collections"].items():
        print(f"  {n}: {c['documents_total']} documents, {len(c['fields'])} field paths")
    print("Wrote artifacts/inspection_report.json")

def cmd_infer(a):
    p = _p()
    prop = p.infer(); rep = p.similarity(); p.create_draft_map(force=a.force)
    print("Proposed tables:", ", ".join(prop["tables"]))
    print(f"Overall match: {rep['overall_percent']}%   counts: {rep['counts']}")
    pend = pending_reviews(p.load_map())
    print(f"{len(pend)} mapping(s) need human review (threshold {p.st.similarity_threshold:.0%}).")
    print("Wrote artifacts/schema_proposal.json, artifacts/schema_map.json (draft), reports/similarity_report.md/.json")

def cmd_similarity(a):
    p = _p(); print(p.store.similarity_md.read_text(encoding="utf-8") if p.store.exists(p.store.similarity_md)
                    else "Run `python -m app.cli infer` first.")

def _show_pending(pend):
    if not pend: print("No mappings need review."); return
    for i, x in enumerate(pend, 1):
        print(f"  {i}. {x['table']}.{x['column']}: {'; '.join(x['reasons'])}")

def interactive_review(p: Pipeline, input_fn=input, out=print):
    while True:
        pend = pending_reviews(p.load_map())
        if not pend:
            out("All mappings reviewed."); return
        x = pend[0]
        out(f"\nReview {x['table']}.{x['column']}: {'; '.join(x['reasons'])}")
        ans = input_fn("  [a]ccept, [r]eject, [t]ype override, [f]k override, [q]uit > ").strip().lower()
        d = {"table": x["table"], "column": x["column"], "by": "cli"}
        if ans == "a": d["action"] = "accept"
        elif ans == "r": d["action"] = "reject"
        elif ans == "t": d.update(action="override", sql_type=input_fn("  new SQL type (e.g. TEXT, BIGINT): ").strip())
        elif ans == "f": d.update(action="override", ref_table=input_fn("  referenced table: ").strip())
        elif ans == "q": return
        else: out("  unknown choice"); continue
        try: p.apply_decisions([d])
        except (SchemaMapError, KeyError) as e: out(f"  error: {e}"); return

def cmd_review(a):
    p = _p()
    if a.accept or a.reject:
        ds = [{"table": t.split(".")[0], "column": t.split(".")[1], "action": "accept", "by": "cli"} for t in a.accept]
        ds += [{"table": t.split(".")[0], "column": t.split(".")[1], "action": "reject", "by": "cli"} for t in a.reject]
        p.apply_decisions(ds); print(f"Applied {len(ds)} decision(s).")
    elif a.list:
        _show_pending(pending_reviews(p.load_map()))
    else:
        interactive_review(p)
    print(f"Still pending: {len(pending_reviews(p.load_map()))}")

def cmd_freeze(a):
    p = _p()
    pend = pending_reviews(p.load_map())
    if pend:
        print("Cannot freeze - these mappings still need review:"); _show_pending(pend); return 1
    if not a.yes and input("Type APPROVE to freeze the schema map: ").strip() != "APPROVE":
        print("Not frozen."); return 1
    sm = p.freeze(a.by); print(f"Frozen revision {sm['revision']}, hash {sm['content_hash'][:16]}...")

def cmd_generate(a):
    m = _p().generate_artifacts(); print("Generated in migrations/generated/:", ", ".join(m["files"]))

def cmd_migrate(a):
    rep = _p().migrate()
    print(f"Migration status: {rep['status']} - {rep['message']}")
    for t, v in rep["tables"].items(): print(f"  {t}: {v}")
    for e in rep["errors"][:10]: print("  ERROR", e)
    return 0 if rep["status"] == "COMPLETED" else 1

def cmd_verify(a):
    rep = _p().verify()
    for c in rep["checks"]: print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: {c['detail']}")
    print(f"\nVERIFICATION: {rep['status']}")
    if rep["status"] != "PASS": print(rep.get("recovery", ""))
    return 0 if rep["status"] == "PASS" else 1

def cmd_crud(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.crud_demo import main; main()

def cmd_rollback(a):
    print(_p().rollback(a.confirm_schema))

def _memory_pipeline() -> Pipeline:
    from sample_data.generate import generate_seed
    from .inspection.source import InMemorySource
    from .migration.sink import InMemorySink
    return Pipeline(load_settings(), InMemorySource(generate_seed(), "memory_demo"), InMemorySink())

def cmd_demo(a):
    """Runs every stage in order. The approval gate is NOT bypassed: pending reviews always need a human."""
    st = load_settings()
    p = _memory_pipeline() if a.memory else Pipeline(st)
    steps = []
    def step(name, fn):
        print(f"\n=== {name} ==="); r = fn(); steps.append(name); return r
    if not a.memory: step("1 seed MongoDB", lambda: cmd_seed(a))
    step("2 inspect", lambda: print(f"{len(p.run_inspection()['collections'])} collections inspected"))
    def infer():
        prop = p.infer(); rep = p.similarity(); p.create_draft_map(force=True)
        print(f"tables: {', '.join(prop['tables'])}\noverall match: {rep['overall_percent']}%  {rep['counts']}")
    step("3 infer schema + similarity", infer)
    def review():
        pend = pending_reviews(p.load_map())
        if pend and not sys.stdin.isatty():
            print("Mappings need human review and no terminal is available:"); _show_pending(pend); raise SystemExit(2)
        if pend: interactive_review(p)
        else: print("no mapping needs review (all above threshold, no unresolved relationships)")
    step("4 review", review)
    def freeze():
        if not a.yes and input("Type APPROVE to freeze the schema map: ").strip() != "APPROVE":
            raise SystemExit("not approved - stopping")
        sm = p.freeze("demo-user"); print(f"frozen hash {sm['content_hash'][:16]}...")
    step("5 approve + freeze", freeze)
    step("6 generate artifacts", lambda: print(", ".join(p.generate_artifacts()["files"])))
    def mig():
        rep = p.migrate(); print(rep["status"], rep["message"])
        if rep["status"] != "COMPLETED": raise SystemExit(1)
    step("7 migrate", mig)
    def ver():
        rep = p.verify(); print("verification:", rep["status"], rep["summary"])
        if rep["status"] != "PASS": raise SystemExit(1)
    step("8 verify", ver)
    if a.memory: print("\n(CRUD demo skipped: it needs PostgreSQL. Run without --memory for the full demo.)")
    else: step("9 CRUD through generated ORM", lambda: cmd_crud(a))
    print("\nDemo finished. Reports are in reports/ and generated files in migrations/generated/.")

def cmd_workflow(a):
    from .ai import get_provider
    from .workflow.graph import run_workflow
    st = load_settings()
    p = _memory_pipeline() if a.memory else Pipeline(st)
    ai, notice = get_provider(st)
    if notice: print("NOTE:", notice)
    def approver(payload):
        print(f"\nHUMAN APPROVAL REQUIRED - overall match {payload['overall_percent']}%, "
              f"{len(payload['pending'])} pending mapping(s)"); _show_pending(payload["pending"])
        decisions = []
        for x in payload["pending"]:
            ans = input(f"  accept {x['table']}.{x['column']}? [y/N] ").strip().lower()
            if ans == "y": decisions.append({"table": x["table"], "column": x["column"], "action": "accept", "by": "cli"})
        if a.yes or input("Type APPROVE to continue to freezing and migration: ").strip() == "APPROVE":
            return {"approve": True, "approved_by": "cli-user", "decisions": decisions}
        return {"approve": False}
    final = run_workflow(p, ai, approver, thread_id=a.thread_id)
    print(f"\nWORKFLOW STATUS: {final.get('status')}  verification={final.get('verification_status')}")
    if final.get("error"): print("error:", final["error"])
    return 0 if final.get("status") == "SUCCESS" else 1

def cmd_info(a):
    for k, v in load_settings().redacted().items(): print(f"{k}: {v}")

def build_parser():
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="SchemaShift: MongoDB -> PostgreSQL migration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_); s.set_defaults(fn=fn); return s
    add("info", cmd_info, "show effective configuration (secrets redacted)")
    add("seed", cmd_seed, "seed the MongoDB demo collections")
    add("inspect", cmd_inspect, "inspect MongoDB collections")
    s = add("infer", cmd_infer, "infer schema, compute similarity, create the DRAFT schema map")
    s.add_argument("--force", action="store_true", help="discard an existing (even frozen) map")
    add("similarity", cmd_similarity, "print the similarity report")
    s = add("review", cmd_review, "review mappings that need approval")
    s.add_argument("--list", action="store_true"); s.add_argument("--accept", nargs="*", default=[], metavar="TABLE.COLUMN")
    s.add_argument("--reject", nargs="*", default=[], metavar="TABLE.COLUMN")
    s = add("freeze", cmd_freeze, "freeze the approved schema map")
    s.add_argument("--by", default="cli-user"); s.add_argument("--yes", action="store_true", help="skip the APPROVE prompt (only valid when nothing is pending)")
    add("generate", cmd_generate, "generate SQL, ETL plan, rollback and ORM artifacts from the frozen map")
    add("migrate", cmd_migrate, "run the data migration")
    add("verify", cmd_verify, "verify migrated data (PASS/FAIL)")
    add("crud", cmd_crud, "run the ORM CRUD demo on PostgreSQL")
    s = add("rollback", cmd_rollback, "drop ONLY the demo tables of the frozen map (needs ALLOW_DESTRUCTIVE_CLEANUP=true)")
    s.add_argument("--confirm-schema", required=True)
    s = add("demo", cmd_demo, "run every stage in order")
    s.add_argument("--memory", action="store_true", help="use the in-memory source/target (no databases needed)")
    s.add_argument("--yes", action="store_true", help="skip the APPROVE prompt for freezing (pending reviews still require a human)")
    s = add("workflow", cmd_workflow, "run the LangGraph workflow with an approval interrupt")
    s.add_argument("--memory", action="store_true"); s.add_argument("--yes", action="store_true")
    s.add_argument("--thread-id", default="schemashift-demo")
    return ap

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except (ConfigError, PipelineError, SchemaMapError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr); return 1
    except Exception as e:
        from .utils.log import redact
        print(f"ERROR ({type(e).__name__}): {redact(str(e))}", file=sys.stderr); return 1

if __name__ == "__main__":
    raise SystemExit(main())
