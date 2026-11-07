"""Error normalization — see spec/03-conventions.md "Error handling"."""

from __future__ import annotations

from typing import Any, Optional

import httpx


class PlatformError(RuntimeError):
    """A normalized platform error: {code, message, retryable} per spec/03-conventions.md."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool,
        http_status: Optional[int] = None,
        retry_after: Optional[str] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.retry_after = retry_after
        suffix = f", retry-after={retry_after}s" if retry_after else ""
        super().__init__(f"[{code}] {message} (retryable={retryable}{suffix})")

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.retry_after:
            d["retry_after"] = self.retry_after
        return d


def _extract_message(body: Any) -> Optional[str]:
    """Pull a human message out of one of the confirmed native error envelope shapes.

    Four distinct shapes are confirmed live (spec/03-conventions.md):
      1. {"id": "WSCPA...", "code": 400, "reason": "...", "message": "..."}
      2. {"_statusCode_": 401, "exception": "...", "message": "Unauthorized"}
      3. {"code": 400, "reason": "Missing required properties...", ...}  (same family as #1)
      4. {"trace": "...", "errors": [{"code": "...", "message": "...", ...}]}  (connections; also used by 460)
    """
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict) and first.get("message"):
            return str(first["message"])
    if body.get("reason"):
        return str(body["reason"])
    if body.get("exception"):
        return str(body["exception"])
    if body.get("message"):
        return str(body["message"])
    return None


def normalize_http_error(resp: httpx.Response) -> PlatformError:
    status = resp.status_code
    try:
        body = resp.json()
    except Exception:
        body = None
    message = _extract_message(body) or (resp.text[:500] if resp.text else resp.reason_phrase) or "Unknown error"
    retry_after = resp.headers.get("Retry-After")

    if status == 400:
        return PlatformError("invalid_request", message, False, status)
    if status == 401:
        # Only ever surfaced to the caller after the transparent re-auth+retry in
        # http.py has already failed once more — see spec/01-auth-and-environments.md.
        return PlatformError("unauthorized", message, False, status)
    if status == 403:
        return PlatformError("forbidden", message, False, status)
    if status == 404:
        return PlatformError("not_found", message, False, status)
    if status == 409:
        return PlatformError("conflict", message, False, status)
    if status == 429:
        return PlatformError("rate_limited", message, True, status, retry_after=retry_after)
    if status == 460:
        # Non-standard status, confirmed live: create_connection actively tests
        # credentials against the external system. See spec/02-tools.md.
        return PlatformError("connection_test_failed", message, False, status)
    if 500 <= status < 600:
        return PlatformError("upstream_error", message, True, status)
    return PlatformError("unknown_error", message, False, status)
