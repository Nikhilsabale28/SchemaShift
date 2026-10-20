"""Configuration from environment variables / .env (stdlib only, so the core runs without extra packages)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

PLACEHOLDER_PASSWORD = "YOUR_POSTGRES_PASSWORD"
BASE_DIR = Path(__file__).resolve().parent.parent

class ConfigError(Exception):
    pass

def _parse_env_file(path: Path) -> dict:
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def _bool(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class Settings:
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "schemashift_demo"
    mongo_collections: tuple = ("customers", "products", "orders")
    postgres_url: str = f"postgresql+psycopg://postgres:{PLACEHOLDER_PASSWORD}@localhost:5432/schemashift_target"
    target_schema: str = "schemashift_demo"
    ai_provider: str = "mock"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout: float = 30.0
    similarity_threshold: float = 0.75
    batch_size: int = 500
    sample_size: int = 1000
    sample_seed: int = 42
    allow_destructive_cleanup: bool = False
    checkpoint_db: str = ""
    base_dir: Path = field(default=BASE_DIR)

    @property
    def artifacts_dir(self): return self.base_dir / "artifacts"
    @property
    def reports_dir(self): return self.base_dir / "reports"
    @property
    def generated_dir(self): return self.base_dir / "migrations" / "generated"

    def redacted(self) -> dict:
        from .utils.log import redact
        d = {k: (v if not isinstance(v, Path) else str(v)) for k, v in self.__dict__.items()}
        d["openai_api_key"] = "***" if self.openai_api_key else ""
        d["postgres_url"] = redact(self.postgres_url)
        d["mongo_uri"] = redact(self.mongo_uri)
        return d

    def require_postgres_configured(self):
        if PLACEHOLDER_PASSWORD in self.postgres_url:
            raise ConfigError("POSTGRES_URL still contains the placeholder password. Edit .env and use the "
                              "password you chose when installing PostgreSQL.")

def load_settings(env_file: str | Path | None = None, environ: dict | None = None) -> Settings:
    env = dict(_parse_env_file(Path(env_file) if env_file else BASE_DIR / ".env"))
    env.update(os.environ if environ is None else environ)
    def num(key, default, cast):
        raw = env.get(key)
        if raw in (None, ""):
            return default
        try:
            return cast(raw)
        except ValueError:
            raise ConfigError(f"{key} must be a {cast.__name__}, got {raw!r}")
    thr = num("SIMILARITY_THRESHOLD", 0.75, float)
    if thr > 1:
        thr = thr / 100.0
    if not 0 < thr <= 1:
        raise ConfigError("SIMILARITY_THRESHOLD must be between 0 and 1 (or 1-100 as a percentage)")
    provider = env.get("AI_PROVIDER", "mock").strip().lower()
    if provider not in {"mock", "openai"}:
        raise ConfigError("AI_PROVIDER must be 'mock' or 'openai'")
    d = Settings()
    timeout = num("OPENAI_TIMEOUT", d.openai_timeout, float)
    if not isfinite(timeout) or timeout <= 0:
        raise ConfigError("OPENAI_TIMEOUT must be a positive finite number")
    model = env.get("OPENAI_MODEL", d.openai_model).strip()
    if not model:
        raise ConfigError("OPENAI_MODEL must not be empty")
    colls = tuple(c.strip() for c in env.get("MONGO_COLLECTIONS", ",".join(d.mongo_collections)).split(",") if c.strip())
    batch = num("BATCH_SIZE", d.batch_size, int)
    if batch < 1:
        raise ConfigError("BATCH_SIZE must be >= 1")
    return Settings(
        mongo_uri=env.get("MONGO_URI", d.mongo_uri), mongo_db=env.get("MONGO_DB", d.mongo_db),
        mongo_collections=colls, postgres_url=env.get("POSTGRES_URL", d.postgres_url),
        target_schema=env.get("TARGET_SCHEMA", d.target_schema), ai_provider=provider,
        openai_api_key=env.get("OPENAI_API_KEY", ""), openai_model=model,
        openai_timeout=timeout,
        similarity_threshold=thr, batch_size=batch,
        sample_size=num("SAMPLE_SIZE", d.sample_size, int), sample_seed=num("SAMPLE_SEED", d.sample_seed, int),
        allow_destructive_cleanup=_bool(env.get("ALLOW_DESTRUCTIVE_CLEANUP", "false")),
        checkpoint_db=env.get("CHECKPOINT_DB", ""))
