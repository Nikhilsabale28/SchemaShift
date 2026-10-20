import dataclasses, tempfile
from pathlib import Path
from app.config import load_settings
from app.pipeline import Pipeline
from app.inspection.source import InMemorySource
from app.migration.sink import InMemorySink
from sample_data.generate import generate_seed

def make_pipeline(threshold=None, docs=None, sink=None):
    st = dataclasses.replace(load_settings(environ={}), base_dir=Path(tempfile.mkdtemp()),
                             **({"similarity_threshold": threshold} if threshold else {}))
    return Pipeline(st, InMemorySource(docs if docs is not None else generate_seed()), sink or InMemorySink())

def frozen_pipeline(**kw):
    p = make_pipeline(**kw)
    p.run_inspection(); p.infer(); p.similarity(); p.create_draft_map(); p.freeze("tester"); p.generate_artifacts()
    return p
