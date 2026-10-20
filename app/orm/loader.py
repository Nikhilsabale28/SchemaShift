
"""Load generated models while registering the module for SQLAlchemy."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_models(path):
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found - run `python -m app.cli generate` first"
        )

    module_name = "schemashift_generated_models"
    spec = importlib.util.spec_from_file_location(module_name, path)

    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load generated models from {path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod

    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(module_name, None)
        raise

    return mod
