"""Authentication flows — see spec/01-auth-and-environments.md.

Three confirmed-live flows:
  - CPD ZenApiKey (default, no round-trip, never expires)
  - CPD bearer via /icp4d-api/v1/authorize (fallback, 12h token)
  - SaaS IAM apikey exchange (~1h token, no refresh token — re-exchange apikey)
"""

from __future__ import annotations

import base64
import time
from typing import Optional

import httpx

from .environments import resolve_saas
from .errors import PlatformError
from .session import SessionState

_CPD_AUTHORIZE_PATH = "/icp4d-api/v1/authorize"
_CPD_BEARER_LIFETIME_SECONDS = 12 * 60 * 60  # confirmed live: exp - iat == 43200s
_HTTP_TIMEOUT = 30.0


async def authenticate_session(
    state: SessionState,
    *,
    environment: str,
    region_or_url: Optional[str],
    apikey: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    prefer_zen_api_key: bool = True,
    stage: str = "prod",
    verify_ssl: bool = True,
    ca_bundle_path: Optional[str] = None,
) -> None:
    """Resolve the target + perform the appropriate auth flow, mutating `state` in place.

    Never returns or logs the credential material itself.

    `verify_ssl`/`ca_bundle_path` govern TLS verification for calls to the
    resolved `base_url` only (the CPD cluster itself, including its own
    /v1/authorize endpoint) — not the SaaS IAM token endpoint, which is
    always a public IBM-operated host and is always verified.
    """
    if environment not in ("saas", "cpd"):
        raise PlatformError("invalid_request", "`environment` must be 'saas' or 'cpd'.", False)

    # Set before dispatching so both flows (and their own httpx calls) can read it off `state`.
    state.verify_ssl = verify_ssl
    state.ca_bundle_path = ca_bundle_path

    if environment == "cpd":
        await _authenticate_cpd(
            state,
            base_url=region_or_url,
            apikey=apikey,
            username=username,
            password=password,
            prefer_zen_api_key=prefer_zen_api_key,
        )
    else:
        await _authenticate_saas(state, region=region_or_url, apikey=apikey, stage=stage)

    # Retain only what's needed to support one transparent re-auth on 401.
    state.apikey = apikey
    state.username = username
    state.password = password
    state.stage = stage
    state.region_or_url = region_or_url
    state.prefer_zen_api_key = prefer_zen_api_key


async def _authenticate_cpd(
    state: SessionState,
    *,
    base_url: Optional[str],
    apikey: Optional[str],
    username: Optional[str],
    password: Optional[str],
    prefer_zen_api_key: bool,
) -> None:
    if not base_url:
        raise PlatformError("invalid_request", "`region_or_url` (the CPD cluster base URL) is required for environment='cpd'.", False)
    if not username:
        raise PlatformError("invalid_request", "`username` is required for environment='cpd'.", False)

    base_url = base_url.rstrip("/")
    state.environment = "cpd"
    state.base_url = base_url

    # Prefer ZenApiKey whenever an apikey (not just a password) is available —
    # confirmed live: needs no round-trip and never expires.
    if apikey and prefer_zen_api_key:
        token_bytes = f"{username}:{apikey}".encode("utf-8")
        state.zen_header = "ZenApiKey " + base64.b64encode(token_bytes).decode("ascii")
        state.auth_method = "zen_api_key"
        state.token = None
        state.expires_at = None
        return

    if not apikey and not password:
        raise PlatformError(
            "invalid_request",
            "environment='cpd' requires either `apikey` (for ZenApiKey) or `password` "
            "(for the /v1/authorize fallback).",
            False,
        )

    body: dict = {"username": username}
    if password:
        body["password"] = password
    else:
        # Confirmed live: the field must be named `api_key`, not `apikey`.
        body["api_key"] = apikey

    # `base_url` is the customer's own CPD cluster — commonly a self-signed
    # or internal-CA certificate, hence honoring state.verify_param() here.
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, verify=state.verify_param()) as client:
        resp = await client.post(base_url + _CPD_AUTHORIZE_PATH, json=body)

    if resp.status_code != 200:
        # Confirmed live failure shape: {"_statusCode_":401,"exception":"...","message":"Unauthorized"}
        from .errors import normalize_http_error

        raise normalize_http_error(resp)

    payload = resp.json()
    token = payload.get("token")
    if not token:
        raise PlatformError("upstream_error", "CPD /v1/authorize succeeded but returned no token.", False)

    state.zen_header = None
    state.token = token
    state.auth_method = "cpd_bearer"
    state.expires_at = time.time() + _CPD_BEARER_LIFETIME_SECONDS


async def _authenticate_saas(state: SessionState, *, region: Optional[str], apikey: Optional[str], stage: str) -> None:
    if not apikey:
        raise PlatformError("invalid_request", "`apikey` is required for environment='saas'.", False)

    base_url, iam_token_url = resolve_saas(region, stage)

    form = {
        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
        "apikey": apikey,
    }
    headers = {"Accept": "application/json"}
    # Always verified (default verify=True) — this is a public IBM-operated IAM
    # host, not the customer's own base_url, so verify_ssl/ca_bundle_path never
    # apply here even if the caller disabled verification for the CPD/SaaS
    # platform host itself.
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(iam_token_url, data=form, headers=headers)

    if resp.status_code != 200:
        from .errors import normalize_http_error

        raise normalize_http_error(resp)

    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise PlatformError("upstream_error", "IAM token exchange succeeded but returned no access_token.", False)

    state.environment = "saas"
    state.base_url = base_url
    state.zen_header = None
    state.token = access_token
    state.auth_method = "iam"
    expires_in = payload.get("expires_in", 3600)
    state.expires_at = time.time() + float(expires_in)


async def reauthenticate(state: SessionState) -> None:
    """One transparent re-auth attempt on a 401, per spec/01-auth-and-environments.md.

    Not applicable to zen_api_key (it doesn't expire) — callers should not
    invoke this for that auth method.
    """
    if state.auth_method == "zen_api_key":
        raise PlatformError("unauthorized", "ZenApiKey was rejected; it does not expire, so no re-auth is possible. Re-run `authenticate`.", False)

    await authenticate_session(
        state,
        environment=state.environment,  # type: ignore[arg-type]
        region_or_url=state.region_or_url,
        apikey=state.apikey,
        username=state.username,
        password=state.password,
        prefer_zen_api_key=state.prefer_zen_api_key,
        stage=state.stage,
        verify_ssl=state.verify_ssl,
        ca_bundle_path=state.ca_bundle_path,
    )
