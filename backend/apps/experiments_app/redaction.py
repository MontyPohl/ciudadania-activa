from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEYS = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|service[_-]?role|signed[_-]?url|document[_-]?number)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_SIGNED_QUERY = re.compile(r"(?i)([?&](?:token|signature|sig|key)=)[^&#\s]+")


def redact(value: Any) -> Any:
    """Redacta secretos y PII común sin mutar el valor recibido."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SECRET_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact(item) for item in value]
    if isinstance(value, str):
        result = _BEARER.sub(REDACTED, value)
        result = _JWT.sub(REDACTED, result)
        result = _EMAIL.sub("[REDACTED:EMAIL]", result)
        return _SIGNED_QUERY.sub(r"\1[REDACTED]", result)
    return value


def contains_secret(value: Any) -> bool:
    """Detector conservador usado antes de persistir evidencia."""
    if isinstance(value, Mapping):
        return any(
            (_SECRET_KEYS.search(str(key)) is not None and item != REDACTED)
            or contains_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(contains_secret(item) for item in value)
    if isinstance(value, str):
        return bool(_BEARER.search(value) or _JWT.search(value) or _SIGNED_QUERY.search(value))
    return False
