"""Thin HTTP layer over the platform REST API: auth headers, 401 retry, error normalization.

See spec/01-auth-and-environments.md (auth headers, one-shot 401 retry) and
spec/03-conventions.md (error normalization table).
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .auth import reauthenticate
from .errors import PlatformError, normalize_http_error
from .session import SessionState

_HTTP_TIMEOUT = 30.0


class PlatformClient:
    """Talks directly to the platform REST API for the given connection's session."""

    def __init__(self, state: SessionState) -> None:
        self.state = state

    def _headers(self) -> dict[str, str]:
        if not self.state.environment or not self.state.base_url:
            raise PlatformError(
                "not_authenticated",
                "No active session. Call the `authenticate` tool first (or set the WX_* env vars).",
                False,
            )
        if self.state.auth_method == "zen_api_key":
            if not self.state.zen_header:
                raise PlatformError("not_authenticated", "Session is missing its ZenApiKey header.", False)
            return {"Authorization": self.state.zen_header}
        if not self.state.token:
            raise PlatformError("not_authenticated", "Session is missing its bearer token.", False)
        return {"Authorization": f"Bearer {self.state.token}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        _retry_on_401: bool = True,
    ) -> Optional[dict]:
        headers = self._headers()  # raises PlatformError("not_authenticated", ...) before touching base_url
        url = self.state.base_url.rstrip("/") + path  # type: ignore[union-attr]

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, verify=self.state.verify_param()) as client:
                resp = await client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise PlatformError("network_error", f"Request to {path} timed out: {exc}", True) from exc
        except httpx.TransportError as exc:
            raise PlatformError("network_error", f"Network error calling {path}: {exc}", True) from exc

        if resp.status_code == 401 and _retry_on_401 and self.state.auth_method != "zen_api_key":
            await reauthenticate(self.state)
            return await self.request(method, path, params=params, json_body=json_body, _retry_on_401=False)

        if resp.is_success:
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return None

        raise normalize_http_error(resp)

    async def raw_put(self, url: str, content: bytes) -> httpx.Response:
        """PUT raw bytes to an arbitrary (e.g. signed) URL — used by upload_asset_file.

        Honors the session's TLS settings too, since a signed upload URL on a
        CPD cluster is typically still served from that same cluster."""
        try:
            async with httpx.AsyncClient(timeout=60.0, verify=self.state.verify_param()) as client:
                resp = await client.put(url, content=content)
        except httpx.TimeoutException as exc:
            raise PlatformError("network_error", f"Upload PUT timed out: {exc}", True) from exc
        except httpx.TransportError as exc:
            raise PlatformError("network_error", f"Network error during upload PUT: {exc}", True) from exc
        if not resp.is_success:
            raise normalize_http_error(resp)
        return resp
