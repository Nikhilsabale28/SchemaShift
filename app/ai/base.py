"""AI provider contract. AI output is ADVISORY ONLY: it is returned as labelled text/suggestions and is
never applied to a schema map, never executed as SQL/Python, and never changes a score or verification result."""
from __future__ import annotations
import json, re
from dataclasses import dataclass, field, asdict
from math import isfinite

class AIError(Exception):
    pass

@dataclass
class AIResult:
    provider: str
    label: str
    ok: bool
    content: dict = field(default_factory=dict)
    error: str = ""
    advisory: bool = True
    def as_dict(self): return asdict(self)

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_LONGNUM = re.compile(r"\d{5,}")
_CONNECTION_STRING = re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?)://\S+",
                                re.IGNORECASE)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*\S+"
)

def scrub_message(msg: str) -> str:
    """Remove quoted values / long numbers so document contents are not sent to an external LLM."""
    from ..utils.log import redact
    text = str(msg)
    text = re.sub(r"(?i)\b(bearer)\s+\S+", r"\1 <redacted>", text)
    text = _CONNECTION_STRING.sub("<connection-string>", text)
    text = _SENSITIVE_ASSIGNMENT.sub("<redacted>", text)
    text = redact(text)
    return _LONGNUM.sub("<n>", _QUOTED.sub("'<value>'", text))[:500]

def parse_json_defensive(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if not m: raise AIError("model response was not valid JSON")
        try: obj = json.loads(m.group(0))
        except json.JSONDecodeError as e: raise AIError("model response was not valid JSON") from e
    if not isinstance(obj, dict):
        raise AIError("model response JSON was not an object")
    return obj

def validate_field_row(row: dict) -> dict:
    need = {"source_path", "target", "score", "name", "type", "structure"}
    if not isinstance(row, dict) or not need <= set(row):
        raise AIError(f"field row must contain {sorted(need)}")
    if not all(isinstance(row[k], str) for k in ("source_path", "target")):
        raise AIError("field row source_path and target must be strings")
    for key in ("score", "name", "type", "structure"):
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
            raise AIError(f"field row {key} must be a number between 0 and 1")
    result = {k: row[k] for k in need}
    if isinstance(row.get("status"), str):
        result["status"] = row["status"][:40]
    if isinstance(row.get("review_reasons"), list):
        result["review_reasons"] = [scrub_message(reason) for reason in row["review_reasons"][:10]
                                    if isinstance(reason, str)]
    return result

def summarize_errors(errors: list, limit: int = 50) -> list:
    if not isinstance(errors, list): raise AIError("errors must be a list")
    out = []
    for e in errors[:limit]:
        if not isinstance(e, dict): raise AIError("each error must be an object")
        out.append({"collection": scrub_message(e.get("collection", "")), "path": scrub_message(e.get("path", "")),
                    "message": scrub_message(e.get("message", ""))})
    return out

class AIProvider:
    name = "base"
    is_llm = False
    def label(self):
        return f"AI-generated suggestion ({self.name}{'' if self.is_llm else ' - deterministic mock, not a real LLM'}); advisory only"
    def suggest_mappings(self, schema_summary: dict) -> AIResult: raise NotImplementedError
    def explain_field(self, field_row: dict) -> AIResult: raise NotImplementedError
    def diagnose_failure(self, errors: list) -> AIResult: raise NotImplementedError
    def summarize_risks(self, summary: dict) -> AIResult: raise NotImplementedError
