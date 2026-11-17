"""
Strips sensitive values from data before it is written to logs, artifacts or evidence files.
Called in the executor before emitting ActionEvents and in the artifact builder before writing
TypeAction values.
"""

from __future__ import annotations
import re
from typing import Any

_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "secret", "token", "ssn", "credential", "api_key", "apikey", 
    "authorization", "access_token", "refresh_token",
})

_SENSITIVE_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\d{3}-\d{2}-\d{4}$"),         # SSN
    re.compile(r"^\d{9}$"),                     # SSN without dashes
    re.compile(r"^[A-Za-z0-9+/]{20,}={0,2}$"),  # base64 token
]

REDACTED = "[REDACTED]"

def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Shallow-copy dict with sensitive values replaced with REDACTED."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _SENSITIVE_KEYS:
            result[key]= REDACTED
        elif isinstance(value, str) and _looks_sensitive(value):
            result[key] = REDACTED
        elif data.get("sensitive") and key == "value":
            result[key] = REDACTED
        else:
            result[key] = value
    return result

def redact_value(value: str, *, sensitive: bool = False) -> str:
    """Redact a single string value if it looks sensitive."""
    if sensitive:
        return REDACTED
    if _looks_sensitive(value):
        return REDACTED
    return value

def _looks_sensitive(value: str) -> bool:
    return any(p.match(value) for p in _SENSITIVE_VALUE_PATTERNS)