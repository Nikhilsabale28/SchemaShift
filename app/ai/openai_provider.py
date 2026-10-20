"""Optional OpenAI provider (official SDK, Responses API, openai>=1.66).

Safety contract (enforced here, not by the LLM):
- Only schema metadata and scrubbed error patterns are sent — never raw documents,
  credentials, connection strings, or customer data.
- All responses are treated as untrusted advisory text; they are validated before
  being returned and are never applied to the schema map automatically.
- The LLM cannot change similarity scores, freeze the map, execute SQL/Python,
  run migrations, or override verification results.
- store=False prevents responses from being saved in OpenAI's conversation history.
"""
from __future__ import annotations
import json
from math import isfinite
import re
from .base import AIProvider, AIResult, AIError, parse_json_defensive, validate_field_row, summarize_errors
from ..utils.log import get_logger, redact

log = get_logger("schemashift.ai")

_SYSTEM = (
    "You assist with a MongoDB-to-PostgreSQL migration review. "
    "You only give advisory suggestions. "
    "Respond with a single JSON object and nothing else. "
    "Never output SQL or code to execute. "
    "Never state a numeric match percentage as authoritative."
)
_MAX_INPUT = 20_000   # characters sent to the model
_MAX_OUTPUT = 1_024   # max output tokens
# JSON object mode: instructs the model to return valid JSON.
_TEXT_FORMAT = {"format": {"type": "json_object"}}
_SUGGESTION_TYPES = {"relationship", "type", "structure"}
_MAX_ITEMS = 100
_MAX_TEXT = 2_000
_ERROR_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

_CREDIT_CODES = {
    "credit_balance_exhausted", "insufficient_credits", "no_credits",
}
_SPEND_LIMIT_CODES = {
    "billing_hard_limit_reached", "billing_limit_reached",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "organization_spend_limit_reached", "project_spend_limit_reached",
    "organization_budget_exceeded", "project_budget_exceeded",
    "organization_usage_limit_exceeded", "project_usage_limit_exceeded",
    "organization_usage_limit_reached", "project_usage_limit_reached",
}
_QUOTA_CODES = {
    "insufficient_quota", "quota_exceeded", "usage_quota_exceeded",
    "exceeded_quota", "usage_limit_reached",
}


def _safe_error_identifier(value) -> str | None:
    if (isinstance(value, str) and _ERROR_IDENTIFIER.fullmatch(value)
            and not value.lower().startswith(("sk-", "bearer"))
            and redact(value) == value):
        return value
    return None


def _rate_limit_details(exc) -> tuple[str | None, str | None, int | None]:
    """Read only short structured identifiers; never include SDK messages or response bodies."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}

    code = _safe_error_identifier(getattr(exc, "code", None))
    error_type = _safe_error_identifier(getattr(exc, "type", None))
    code = code or _safe_error_identifier(error.get("code"))
    error_type = error_type or _safe_error_identifier(error.get("type"))

    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        status = None
    return code, error_type, status


def _rate_limit_error_message(exc) -> str:
    code, error_type, status = _rate_limit_details(exc)
    identifiers = []
    if status is not None:
        identifiers.append(f"HTTP {status}")
    if code is not None:
        identifiers.append(f"code={code}")
    if error_type is not None:
        identifiers.append(f"type={error_type}")
    detail = f" ({'; '.join(identifiers)})" if identifiers else ""
    identifiers_lower = {value.lower() for value in (code, error_type) if value}

    if identifiers_lower & _SPEND_LIMIT_CODES:
        advice = (
            "organization or project spend limit reached; ask an organization/project admin to review "
            "the configured budget or usage limit"
        )
    elif identifiers_lower & _CREDIT_CODES:
        advice = (
            "OpenAI credit balance is exhausted; add credits or review the account's billing balance"
        )
    elif identifiers_lower & _QUOTA_CODES:
        advice = (
            "OpenAI usage quota is exhausted or unavailable; review the account plan, usage quota, "
            "and billing status"
        )
    else:
        advice = (
            "temporary request or token rate limit; wait and retry, reduce request frequency, "
            "or lower token usage"
        )
    return f"OpenAI rate limit response{detail}: {advice}"


def _classify_error(exc) -> str:
    """Return a short, redacted, human-readable description of an SDK error."""
    try:
        import openai
        if isinstance(exc, openai.AuthenticationError):
            return "authentication failed — check OPENAI_API_KEY"
        if isinstance(exc, openai.RateLimitError):
            return _rate_limit_error_message(exc)
        if isinstance(exc, openai.APITimeoutError):
            return "request timed out — increase OPENAI_TIMEOUT or retry"
        if isinstance(exc, openai.APIConnectionError):
            return "network error connecting to OpenAI — check internet connectivity"
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            return f"OpenAI API request failed (HTTP {status})" if status else "OpenAI API request failed"
        if isinstance(exc, openai.OpenAIError):
            return f"OpenAI SDK request failed ({type(exc).__name__})"
    except ImportError:
        pass
    if isinstance(exc, AIError):
        return redact(str(exc))[:200]
    return f"unexpected provider error ({type(exc).__name__})"


def _text(value, key: str, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise AIError(f"response field '{key}' must be a non-empty string")
    if len(value) > _MAX_TEXT:
        raise AIError(f"response field '{key}' is too long")
    return value.strip()


def _validate_suggestions(data: dict, schema: dict) -> dict:
    suggestions = data.get("suggestions")
    if not isinstance(suggestions, list) or len(suggestions) > _MAX_ITEMS:
        raise AIError("response field 'suggestions' must be a list of at most 100 items")
    tables = schema["tables"]
    validated = []
    for item in suggestions:
        if not isinstance(item, dict) or set(item) != {"table", "column", "type", "suggestion", "reason"}:
            raise AIError("each suggestion must contain only table, column, type, suggestion, and reason")
        table = item["table"]
        column = item["column"]
        kind = item["type"]
        if not isinstance(table, str) or table not in tables:
            raise AIError("suggestion references an unknown table")
        if column is not None and (
            not isinstance(column, str) or column not in tables[table]["columns"]
        ):
            raise AIError("suggestion references an unknown column")
        if not isinstance(kind, str) or kind not in _SUGGESTION_TYPES:
            raise AIError("suggestion type must be relationship, type, or structure")
        validated.append({
            "table": table,
            "column": column,
            "type": kind,
            "suggestion": _text(item["suggestion"], "suggestion"),
            "reason": _text(item["reason"], "reason"),
        })
    return {"suggestions": validated}


def _validate_explanation(data: dict) -> dict:
    return {"explanation": _text(data.get("explanation"), "explanation")}


def _validate_diagnosis(data: dict) -> dict:
    diagnosis = _text(data.get("diagnosis"), "diagnosis")
    recommendations = data.get("recommendations")
    if not isinstance(recommendations, list) or len(recommendations) > _MAX_ITEMS:
        raise AIError("response field 'recommendations' must be a list of at most 100 items")
    return {"diagnosis": diagnosis,
            "recommendations": [_text(x, "recommendations[]") for x in recommendations]}


def _validate_risks(data: dict) -> dict:
    risks = data.get("risks")
    if not isinstance(risks, list) or len(risks) > _MAX_ITEMS:
        raise AIError("response field 'risks' must be a list of at most 100 items")
    return {"risks": [_text(x, "risks[]") for x in risks]}


def _schema_metadata(schema_summary: dict) -> dict:
    if not isinstance(schema_summary, dict) or not isinstance(schema_summary.get("tables"), dict):
        raise AIError("schema summary must contain a tables object")
    safe_tables = {}
    for table, spec in list(schema_summary["tables"].items())[:_MAX_ITEMS]:
        if not isinstance(table, str) or not isinstance(spec, dict):
            raise AIError("schema summary tables must map names to objects")
        columns = spec.get("columns", {})
        safe_columns = {}
        if isinstance(columns, dict):
            entries = columns.items()
        elif isinstance(columns, list):
            entries = ((col.get("name"), col) for col in columns if isinstance(col, dict))
        else:
            raise AIError("schema summary columns must be an object or list")
        for name, info in list(entries)[:_MAX_ITEMS]:
            if not isinstance(name, str) or not isinstance(info, dict):
                raise AIError("schema summary columns must have names and metadata")
            safe_columns[name] = {
                flag: bool(info.get(flag, False))
                for flag in ("unresolved_fk", "mixed_types")
                if flag in info
            }
        kind = spec.get("kind")
        safe_tables[table] = {
            "kind": kind[:50] if isinstance(kind, str) else "",
            "columns": safe_columns,
        }
    return {"tables": safe_tables}


def _risk_metadata(summary: dict) -> dict:
    if not isinstance(summary, dict):
        raise AIError("risk summary must be an object")
    safe = {}
    for key in ("needs_review", "unmatched", "overall_percent"):
        if key in summary:
            value = summary[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not isfinite(value) or value < 0
                    or (key == "overall_percent" and value > 100)):
                raise AIError(f"risk summary '{key}' must be a non-negative number")
            safe[key] = value
    return safe


class OpenAIProvider(AIProvider):
    name = "openai"
    is_llm = True

    def __init__(self, api_key: str, model: str, timeout: float = 30.0):
        if not isinstance(api_key, str) or not api_key.strip():
            raise AIError("OPENAI_API_KEY is not set")
        if not isinstance(model, str) or not model.strip():
            raise AIError("OPENAI_MODEL must not be empty")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise AIError("openai package not installed (pip install openai>=1.66)") from e
        self.model = model.strip()
        self._client = OpenAI(api_key=api_key.strip(), timeout=timeout, max_retries=1)

    def _ask(self, task: str, payload: dict, keys: list, validator) -> AIResult:
        prompt = (
            f"Task: {task}. Return only valid JSON as one object with required keys {keys}. "
            "Do not include any other keys or invent deterministic scores.\n"
            + json.dumps(payload, default=str)[:_MAX_INPUT]
        )
        try:
            resp = self._client.responses.create(
                model=self.model,
                instructions=_SYSTEM,
                input=prompt,
                max_output_tokens=_MAX_OUTPUT,
                temperature=0,
                text=_TEXT_FORMAT,
                store=False,
            )
            raw = resp.output_text
            if not raw:
                raise AIError("model returned an empty response")
            data = parse_json_defensive(raw)
            if not all(k in data for k in keys):
                raise AIError(f"response lacked required keys {keys}")
            content = validator(data)
            return AIResult(self.name, self.label(), True, content)
        except Exception as exc:
            msg = _classify_error(exc)
            log.warning("OpenAI call failed: %s", msg)
            return AIResult(self.name, self.label(), False, error=msg)

    def suggest_mappings(self, schema_summary: dict) -> AIResult:
        schema = _schema_metadata(schema_summary)
        return self._ask(
            "suggest readable names, relationships, and embed-versus-table design choices from schema metadata",
            {"schema": schema}, ["suggestions"], lambda data: _validate_suggestions(data, schema),
        )

    def explain_field(self, field_row: dict) -> AIResult:
        field = validate_field_row(field_row)
        return self._ask(
            "explain in plain language why this field mapping is or is not reliable",
            {"field": field}, ["explanation"], _validate_explanation,
        )

    def diagnose_failure(self, errors: list) -> AIResult:
        safe_errors = summarize_errors(errors)
        return self._ask(
            "diagnose these migration errors and recommend mapping corrections for human review",
            {"errors": safe_errors}, ["diagnosis", "recommendations"], _validate_diagnosis,
        )

    def summarize_risks(self, summary: dict) -> AIResult:
        return self._ask("summarize migration risks", {"summary": _risk_metadata(summary)}, ["risks"],
                         _validate_risks)
