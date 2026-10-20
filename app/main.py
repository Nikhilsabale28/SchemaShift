"""FastAPI application. Start with: uvicorn app.main:app --reload   (Swagger UI at /docs)"""
from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .api.routes import router
from .api.jobs import JobRegistry
from .config import load_settings, ConfigError
from .pipeline import Pipeline, PipelineError
from .schema_map.schema_map import SchemaMapError
from .generation.ddl import GenerationError
from .inspection.source import SourceError
from .migration.sink import SinkError

VERSION = "1.0.0"

def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    app = FastAPI(title="SchemaShift", version=VERSION,
                  description="MongoDB -> PostgreSQL migration with deterministic scoring, approval gates and verification")
    app.state.pipeline = pipeline or Pipeline(load_settings())
    app.state.jobs = JobRegistry()
    app.include_router(router)

    def handler(status):
        async def _h(request: Request, exc: Exception):
            from .utils.log import redact
            return JSONResponse(status_code=status, content={"error": type(exc).__name__, "detail": redact(str(exc))})
        return _h
    for exc, code in ((PipelineError, 409), (SchemaMapError, 409), (GenerationError, 409), (SourceError, 503),
                      (SinkError, 503), (ConfigError, 500), (FileNotFoundError, 404)):
        app.add_exception_handler(exc, handler(code))
    return app

try:
    app = create_app()
except ConfigError as _e:  # surface config problems at startup with a readable message
    raise SystemExit(f"Configuration error: {_e}")
