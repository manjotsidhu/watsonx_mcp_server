"""Generic credential redaction — see spec/03-conventions.md "Security / redaction".

Confirmed live on a real SaaS tenant: get_project/list_projects can return live
IBM Cloud API keys and S3 access/secret keys embedded in
entity.storage.properties.credentials. The spec is explicit that this must be a
generic post-processing pass applied to *every* tool's output, not a per-tool
special case for connection-shaped resources — more resource types may carry
embedded credentials than have been discovered so far.
"""

from __future__ import annotations

from typing import Any

# Field names treated as secret wherever they appear, at any nesting depth,
# case-insensitively. Deliberately broad per the spec's "recursively, any nested
# object matching common secret-key field names" instruction.
_SECRET_KEYS = {
    "credentials",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "access_key_id",
    "secret_access_key",
    "secret_key",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
    "client_secret",
    "auth_header",
    "authorization",
}

_REDACTED = "***REDACTED***"


def redact(value: Any) -> Any:
    """Recursively redact known secret-shaped fields from a JSON-able structure."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SECRET_KEYS:
                out[k] = _REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
