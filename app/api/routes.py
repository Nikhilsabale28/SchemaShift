"""REST API. No endpoint exposes destructive actions (no rollback/cleanup over HTTP)."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request, Response, Query
from fastapi.responses import PlainTextResponse
from ..schemas.api import ApproveRequest, FreezeRequest, InferRequest, ExplainRequest
from ..schema_map.schema_map import pending_reviews
from ..utils.store import Store
from ..ai import get_provider
from ..ai.base import AIError
from ..config import ConfigError

router = APIRouter()

def P(request: Request):
    return request.app.state.pipeline

def _read(path):
    try:
        return Store.read_json(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

def _get_ai(settings):
    try:
        return get_provider(settings)
    except (AIError, ConfigError) as exc:
        raise HTTPException(status_code=503, detail=f"AI provider unavailable: {exc}") from exc

@router.get("/health", tags=["system"])
def health(request: Request, deep: bool = False):
    p = P(request)
    out = {"status": "ok", "ai_provider": p.st.ai_provider, "target_schema": p.st.target_schema}
    if deep:
        checks = {}
        for name, fn in (("source", lambda: p.source.ping()), ("target", lambda: p.sink.ping())):
            try: fn(); checks[name] = "ok"
            except Exception as e: checks[name] = f"error: {type(e).__name__}: {e}"[:200]
        out["dependencies"] = checks
        if any(v != "ok" for v in checks.values()): out["status"] = "degraded"
    return out

@router.get("/api/v1/source/collections", tags=["source"])
def collections(request: Request):
    return {"collections": P(request).source_collections()}

@router.post("/api/v1/inspection/run", tags=["inspection"])
def inspection_run(request: Request):
    rep = P(request).run_inspection()
    return {"source_database": rep["source_database"],
            "collections": {n: {"documents": c["documents_total"], "field_paths": len(c["fields"])}
                            for n, c in rep["collections"].items()}}

@router.get("/api/v1/inspection/report", tags=["inspection"])
def inspection_report(request: Request):
    return _read(P(request).store.inspection)

@router.post("/api/v1/schema/infer", tags=["schema"])
def schema_infer(request: Request, body: InferRequest = InferRequest()):
    p = P(request)
    _read(p.store.inspection)
    prop = p.infer(); rep = p.similarity(); sm = p.create_draft_map(force=body.force)
    return {"tables": sorted(prop["tables"]), "overall_percent": rep["overall_percent"], "counts": rep["counts"],
            "pending_reviews": len(pending_reviews(sm)), "map_status": sm["status"], "map_revision": sm["revision"]}

@router.get("/api/v1/schema-map", tags=["schema"])
def schema_map(request: Request):
    sm = _read(P(request).store.schema_map)
    return {"schema_map": sm, "pending_reviews": pending_reviews(sm)}

@router.get("/api/v1/similarity/report", tags=["similarity"])
def similarity(request: Request, format: str = Query("json", pattern="^(json|markdown)$")):
    p = P(request)
    if format == "markdown":
        if not p.store.exists(p.store.similarity_md): raise HTTPException(404, "similarity report not found - run schema/infer")
        return PlainTextResponse(p.store.similarity_md.read_text(encoding="utf-8"), media_type="text/markdown")
    return _read(p.store.similarity_json)

@router.post("/api/v1/schema/approve", tags=["schema"])
def approve(request: Request, body: ApproveRequest):
    p = P(request)
    try:
        sm = p.apply_decisions([d.model_dump() for d in body.decisions])
    except KeyError as e:
        raise HTTPException(422, f"unknown mapping: {e}")
    return {"pending_reviews": pending_reviews(sm), "map_status": sm["status"]}

@router.post("/api/v1/schema/freeze", tags=["schema"])
def freeze(request: Request, body: FreezeRequest):
    sm = P(request).freeze(body.approved_by)
    return {"map_status": sm["status"], "revision": sm["revision"], "content_hash": sm["content_hash"]}

@router.post("/api/v1/artifacts/generate", tags=["artifacts"])
def generate(request: Request):
    return P(request).generate_artifacts()

@router.get("/api/v1/artifacts/status", tags=["artifacts"])
def artifacts_status(request: Request):
    ok, why = P(request).artifacts_status()
    return {"current": ok, "reason": why}

@router.post("/api/v1/migration/run", status_code=202, tags=["migration"])
def migration_run(request: Request, response: Response):
    p, jobs = P(request), request.app.state.jobs
    ok, why = p.artifacts_status()
    if not ok: raise HTTPException(409, f"cannot migrate: {why}")
    if not jobs.start(lambda progress: p.migrate(progress=progress)):
        raise HTTPException(409, "a migration is already running")
    response.headers["Location"] = "/api/v1/migration/status"
    return {"state": "running", "status_url": "/api/v1/migration/status"}

@router.get("/api/v1/migration/status", tags=["migration"])
def migration_status(request: Request):
    return request.app.state.jobs.snapshot()

@router.post("/api/v1/verification/run", tags=["verification"])
def verification_run(request: Request):
    if request.app.state.jobs.snapshot()["state"] == "running":
        raise HTTPException(409, "migration still running")
    return P(request).verify()

@router.get("/api/v1/verification/report", tags=["verification"])
def verification_report(request: Request):
    return _read(P(request).store.verification_report)

@router.get("/api/v1/orm/models", response_class=PlainTextResponse, tags=["orm"])
def orm_models(request: Request):
    p = P(request)
    ok, why = p.artifacts_status()
    if not ok: raise HTTPException(404, f"no current ORM models: {why}")
    return (p.store.generated / "orm_models.py").read_text(encoding="utf-8")

@router.post("/api/v1/ai/explain", tags=["ai"])
def ai_explain(request: Request, body: ExplainRequest):
    p = P(request)
    sim = _read(p.store.similarity_json)
    row = next((f for f in sim["fields"] if f["table"] == body.table and f["column"] == body.column), None)
    if row is None: raise HTTPException(404, "field not found in similarity report")
    ai, notice = _get_ai(p.st)
    res = ai.explain_field(row).as_dict(); res["notice"] = notice
    return res

@router.post("/api/v1/ai/diagnose", tags=["ai"])
def ai_diagnose(request: Request):
    p = P(request)
    rep = _read(p.store.migration_report)
    ai, notice = _get_ai(p.st)
    res = ai.diagnose_failure(rep.get("errors", [])).as_dict(); res["notice"] = notice
    return res
