"""Deterministic source inspection over an iterable of documents.

Works on plain dicts, so it is unit-testable without MongoDB. The MongoDB
reader (app/inspection/mongo_source.py) just feeds documents into it.
"""
from __future__ import annotations
import random
from collections import Counter
from ..core.types import type_name

def sample_documents(docs: list, sample_size: int, seed: int = 42) -> list:
    """Reproducible sample: same seed + same input order -> same sample."""
    if len(docs) <= sample_size:
        return list(docs)
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(docs)), sample_size))
    return [docs[i] for i in idx]

def _walk(value, path, stats, doc_paths):
    t = type_name(value)
    s = stats.setdefault(path, {"types": Counter(), "present": 0})
    s["types"][t] += 1
    doc_paths.add(path)
    if t == "object":
        for k, v in value.items():
            _walk(v, f"{path}.{k}", stats, doc_paths)
    elif t == "array":
        for item in value:
            _walk(item, f"{path}[]", stats, doc_paths)

def inspect_collection(name: str, docs: list, sample_size: int = 1000, seed: int = 42) -> dict:
    sample = sample_documents(docs, sample_size, seed)
    stats: dict = {}
    for doc in sample:
        seen: set = set()
        for k, v in doc.items():
            _walk(v, k, stats, seen)
        for p in seen:
            stats[p]["present"] += 1
    n = len(sample)
    total = {p_: sum(v["types"].values()) for p_, v in stats.items()}

    def _absent(path, present):
        by_doc = "[]" not in path and present < n
        parent = path.rsplit(".", 1)[0] if "." in path else None
        by_parent = parent in total and total[path] < total[parent]
        return by_doc or by_parent
    fields = {}
    for path in sorted(stats):
        s = stats[path]
        types = dict(sorted(s["types"].items()))
        non_null = {k: v for k, v in types.items() if k != "null"}
        fields[path] = {
            "types": types,
            "present_in_docs": s["present"],
            "occurrence": round(s["present"] / n, 4) if n else 0.0,
            "sometimes_absent": _absent(path, s["present"]),
            "has_null": "null" in types,
            "mixed_types": len(non_null) > 1,
            "is_object": "object" in types,
            "is_array": "array" in types,
        }
    return {"collection": name, "documents_total": len(docs),
            "documents_sampled": n, "sample_seed": seed, "fields": fields}

def inspect_database(collections: dict, sample_size: int = 1000, seed: int = 42) -> dict:
    """collections: name -> list of docs. Returns the machine-readable report."""
    return {"inspection_version": 1,
            "collections": {n: inspect_collection(n, collections[n], sample_size, seed)
                            for n in sorted(collections)}}
