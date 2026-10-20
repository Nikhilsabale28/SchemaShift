"""Name normalization used before any similarity comparison."""
from __future__ import annotations
import re

DEFAULT_ABBREVIATIONS = {
    "qty": "quantity", "amt": "amount", "addr": "address", "cust": "customer",
    "prod": "product", "num": "number", "desc": "description", "dt": "date",
    "ts": "timestamp", "pwd": "password", "tel": "phone", "zip": "postal",
    "zipcode": "postal", "postcode": "postal",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[\s_\-./]+")

def tokenize(name: str, abbreviations: dict[str, str] | None = None) -> list[str]:
    abbr = DEFAULT_ABBREVIATIONS if abbreviations is None else abbreviations
    spaced = _CAMEL.sub(" ", name.strip())
    tokens = [t.lower() for t in _SPLIT.split(spaced) if t]
    return [abbr.get(t, t) for t in tokens]

def normalize_name(name: str, abbreviations: dict[str, str] | None = None) -> str:
    return "_".join(tokenize(name, abbreviations))
