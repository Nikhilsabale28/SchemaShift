"""Deterministic field/table/overall similarity.

Field score = 0.5*name + 0.3*type + 0.2*structure   (weights configurable)
Hard rules:
  * type compatibility < 0.5  -> score capped at 0.60 (unsafe conversion)
  * unresolved foreign key    -> score capped at 0.60
Aggregation: table score = mean of field scores over ALL source fields
(unmatched source fields count as 0). Overall = field-count-weighted mean
over all tables, so missing data always lowers the percentage.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from .normalize import tokenize, normalize_name
from ..core.types import type_compatibility

try:  # optional dependency with deterministic fallback
    from rapidfuzz import fuzz as _fuzz
    def _ratio(a: str, b: str) -> float:
        return _fuzz.ratio(a, b) / 100.0
except ImportError:
    from difflib import SequenceMatcher
    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

@dataclass(frozen=True)
class Weights:
    name: float = 0.5
    type: float = 0.3
    structure: float = 0.2

def name_similarity(a: str, b: str, abbreviations=None) -> float:
    ta, tb = sorted(tokenize(a, abbreviations)), sorted(tokenize(b, abbreviations))
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return round(_ratio(" ".join(ta), " ".join(tb)), 4)

def structure_similarity(source_path, target_table, target_column, source_collection,
                         is_array_child, path_is_nested) -> float:
    """Explicit structural/path rules (no learning)."""
    score = 0.0
    if normalize_name(source_collection) == normalize_name(target_table) or is_array_child:
        score += 0.6
    last = source_path.split(".")[-1].replace("[]", "")
    if normalize_name(last) == normalize_name(target_column):
        score += 0.4
    elif path_is_nested and normalize_name(source_path.replace(".", "_")) == normalize_name(target_column):
        score += 0.4  # flattened path address.city -> address_city
    return min(score, 1.0)

@dataclass
class FieldScore:
    source_path: str
    target: str
    name: float
    type: float
    structure: float
    score: float
    status: str  # matched | guessed | unmatched
    notes: list

    def as_dict(self):
        return asdict(self)

def score_field(source_path, source_type, source_collection, target_table, target_column,
                target_sql_type, *, is_array_child=False, unresolved_fk=False,
                weights: Weights = Weights(), abbreviations=None,
                match_threshold=0.9, guess_threshold=0.5) -> FieldScore:
    nested = "." in source_path
    n = name_similarity(source_path.replace(".", "_") if nested else source_path.replace("[]", ""),
                        target_column, abbreviations)
    if nested:
        n = max(n, name_similarity(source_path.split(".")[-1], target_column, abbreviations) * 0.9)
    t = type_compatibility(source_type, target_sql_type)
    s = structure_similarity(source_path, target_table, target_column, source_collection,
                             is_array_child, nested)
    raw = weights.name * n + weights.type * t + weights.structure * s
    notes = []
    if t < 0.5:
        raw = min(raw, 0.60)
        notes.append(f"type {source_type} -> {target_sql_type} is unsafe (compat {t:.2f}); score capped at 0.60")
    if unresolved_fk:
        raw = min(raw, 0.60)
        notes.append("foreign key target not resolved; score capped at 0.60")
    raw = round(raw, 4)
    status = "matched" if raw >= match_threshold else "guessed" if raw >= guess_threshold else "unmatched"
    if n < 1.0:
        notes.append(f"name similarity {n:.2f} ('{source_path}' vs '{target_column}')")
    return FieldScore(source_path, f"{target_table}.{target_column}", n, t, s, raw, status, notes)

def aggregate(table_scores: dict) -> dict:
    """table -> list of field scores (unmatched source fields must be passed as 0.0)."""
    per_table = {t: (round(sum(v) / len(v), 4) if v else 0.0) for t, v in table_scores.items()}
    total = sum(len(v) for v in table_scores.values())
    overall = round(sum(sum(v) for v in table_scores.values()) / total, 4) if total else 0.0
    return {"per_table": per_table, "overall": overall, "field_count": total}
