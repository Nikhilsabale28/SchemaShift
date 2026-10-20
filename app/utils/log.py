"""Logging helpers with secret redaction."""
from __future__ import annotations
import logging, re

_URI_PW = re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)")
_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")

def redact(text: str) -> str:
    text = _URI_PW.sub(r"\1***\3", str(text))
    return _KEY.sub("sk-***", text)

class RedactFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True

def get_logger(name: str = "schemashift") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.addFilter(RedactFilter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log
