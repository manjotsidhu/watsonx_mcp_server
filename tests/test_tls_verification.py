"""TLS verification for self-signed/internal-CA CPD clusters.

Not part of the spec (which covers API request/response shapes, not client
TLS config) — this is a client-side option added on top, since CPD clusters
commonly serve a certificate a default trust store won't validate.
"""

from __future__ import annotations

import json

import certifi
import httpx
import pytest
import respx
from httpx import Response

# httpx builds a real ssl.SSLContext from `verify=<path>` eagerly at
# AsyncClient construction time (loading and parsing the file), even under
# respx's mocked transport — so tests need a real, valid CA bundle file, not
# a made-up path. certifi's bundle (an httpx dependency, always installed) is
# a convenient stand-in for "the cluster's CA certificate".
_FAKE_CA_BUNDLE_PATH = certifi.where()

from watsonx_platform_mcp.auth import authenticate_session
from watsonx_platform_mcp.bootstrap import ensure_authenticated
from watsonx_platform_mcp.http import PlatformClient
from watsonx_platform_mcp.session import SessionState


@pytest.fixture
def captured_verify_kwargs(monkeypatch):
    """Records the `verify=` kwarg passed to every httpx.AsyncClient(...) call
    made during the test, without disturbing respx's own transport mocking."""
    calls: list = []
    real_init = httpx.AsyncClient.__init__

    def spy_init(self, *args, **kwargs):
        calls.append(kwargs.get("verify"))
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)
    return calls


def test_verify_param_defaults_to_true():
    assert SessionState().verify_param() is True


def test_verify_param_false_when_verify_ssl_disabled():
    assert SessionState(verify_ssl=False).verify_param() is False


def test_ca_bundle_path_takes_precedence_over_verify_ssl():
    state = SessionState(verify_ssl=False, ca_bundle_path="/etc/ssl/my-cluster-ca.pem")
    assert state.verify_param() == "/etc/ssl/my-cluster-ca.pem"


@pytest.mark.asyncio
async def test_cpd_zen_api_key_stores_verify_settings_without_any_network_call(captured_verify_kwargs):
    """ZenApiKey needs no HTTP call at all, but verify_ssl/ca_bundle_path must
    still land on the session so later platform calls honor them."""
    state = SessionState()
    with respx.mock(assert_all_called=True):
        await authenticate_session(
            state,
            environment="cpd",
            region_or_url="https://cpd.example.com",
            apikey="platform-key",
            username="alice",
            verify_ssl=False,
        )
    assert state.verify_ssl is False
    assert state.verify_param() is False
    assert captured_verify_kwargs == []  # no httpx.AsyncClient constructed for ZenApiKey


@pytest.mark.asyncio
@respx.mock
async def test_cpd_bearer_authorize_call_disables_verification_when_requested(captured_verify_kwargs):
    respx.post("https://cpd.example.com/icp4d-api/v1/authorize").mock(
        return_value=Response(200, json={"token": "jwt-token"})
    )
    state = SessionState()
    await authenticate_session(
        state,
        environment="cpd",
        region_or_url="https://cpd.example.com",
        apikey="platform-key",
        username="alice",
        prefer_zen_api_key=False,
        verify_ssl=False,
    )
    assert False in captured_verify_kwargs


@pytest.mark.asyncio
@respx.mock
async def test_cpd_bearer_authorize_call_uses_ca_bundle_path(captured_verify_kwargs):
    respx.post("https://cpd.example.com/icp4d-api/v1/authorize").mock(
        return_value=Response(200, json={"token": "jwt-token"})
    )
    state = SessionState()
    await authenticate_session(
        state,
        environment="cpd",
        region_or_url="https://cpd.example.com",
        apikey="platform-key",
        username="alice",
        prefer_zen_api_key=False,
        ca_bundle_path=_FAKE_CA_BUNDLE_PATH,
    )
    assert _FAKE_CA_BUNDLE_PATH in captured_verify_kwargs


@pytest.mark.asyncio
@respx.mock
async def test_saas_iam_call_always_verified_even_if_verify_ssl_disabled(captured_verify_kwargs):
    """The IAM endpoint is a public IBM-operated host, not the caller's own
    base_url — verify_ssl must never weaken verification there."""
    respx.post("https://iam.cloud.ibm.com/identity/token").mock(
        return_value=Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    state = SessionState()
    await authenticate_session(
        state,
        environment="saas",
        region_or_url="us-south",
        apikey="my-apikey",
        verify_ssl=False,  # should NOT weaken the IAM call
    )
    # The one httpx.AsyncClient constructed here (for the IAM call) must default to verify=True.
    assert captured_verify_kwargs == [None]  # None means httpx's own default (True) was used


@pytest.mark.asyncio
@respx.mock
async def test_platform_client_request_honors_verify_ssl_false(cpd_zen_state: SessionState, captured_verify_kwargs):
    cpd_zen_state.verify_ssl = False
    respx.get("https://cpd.example.com/v2/projects").mock(return_value=Response(200, json={"resources": []}))
    client = PlatformClient(cpd_zen_state)
    await client.request("GET", "/v2/projects")
    assert False in captured_verify_kwargs


@pytest.mark.asyncio
@respx.mock
async def test_platform_client_request_honors_ca_bundle_path(cpd_zen_state: SessionState, captured_verify_kwargs):
    cpd_zen_state.ca_bundle_path = _FAKE_CA_BUNDLE_PATH
    respx.get("https://cpd.example.com/v2/projects").mock(return_value=Response(200, json={"resources": []}))
    client = PlatformClient(cpd_zen_state)
    await client.request("GET", "/v2/projects")
    assert _FAKE_CA_BUNDLE_PATH in captured_verify_kwargs


@pytest.mark.asyncio
@respx.mock
async def test_raw_put_honors_verify_ssl_false(cpd_zen_state: SessionState, captured_verify_kwargs):
    cpd_zen_state.verify_ssl = False
    respx.put("https://cpd.example.com/upload/signed-url").mock(return_value=Response(200))
    client = PlatformClient(cpd_zen_state)
    await client.raw_put("https://cpd.example.com/upload/signed-url", b"data")
    assert False in captured_verify_kwargs


@pytest.mark.asyncio
async def test_env_bootstrap_wires_verify_ssl_and_ca_bundle_path(monkeypatch, ctx_factory):
    monkeypatch.setenv("WX_ENVIRONMENT", "cpd")
    monkeypatch.setenv("WX_URL_OR_REGION", "https://cpd.example.com")
    monkeypatch.setenv("WX_USERNAME", "alice")
    monkeypatch.setenv("WX_APIKEY", "platform-key")
    monkeypatch.setenv("WX_VERIFY_SSL", "false")

    ctx = ctx_factory(SessionState())
    with respx.mock(assert_all_called=True):  # ZenApiKey path: no HTTP call expected
        state = await ensure_authenticated(ctx)
    assert state.environment == "cpd"
    assert state.verify_ssl is False


@pytest.mark.asyncio
async def test_env_bootstrap_ca_bundle_path(monkeypatch, ctx_factory):
    monkeypatch.setenv("WX_ENVIRONMENT", "cpd")
    monkeypatch.setenv("WX_URL_OR_REGION", "https://cpd.example.com")
    monkeypatch.setenv("WX_USERNAME", "alice")
    monkeypatch.setenv("WX_APIKEY", "platform-key")
    monkeypatch.setenv("WX_CA_BUNDLE_PATH", "/etc/ssl/my-cluster-ca.pem")

    ctx = ctx_factory(SessionState())
    with respx.mock(assert_all_called=True):
        state = await ensure_authenticated(ctx)
    assert state.ca_bundle_path == "/etc/ssl/my-cluster-ca.pem"


def _warning_recorder(warnings: list):
    async def _warning(message: str, **extra):
        warnings.append(message)

    return _warning


@pytest.mark.asyncio
@respx.mock
async def test_authenticate_tool_warns_and_reports_tls_verify_when_disabled(ctx_factory):
    from watsonx_platform_mcp.tools import auth_tool

    warnings: list = []
    ctx = ctx_factory(SessionState())
    ctx.warning = _warning_recorder(warnings)

    result = await auth_tool.authenticate(
        ctx,
        environment="cpd",
        region_or_url="https://cpd.example.com",
        apikey="platform-key",
        username="alice",
        verify_ssl=False,
    )
    assert result["tls_verify"] is False
    assert warnings and "DISABLED" in warnings[0]


@pytest.mark.asyncio
@respx.mock
async def test_authenticate_tool_does_not_warn_when_verified(ctx_factory):
    from watsonx_platform_mcp.tools import auth_tool

    warnings: list = []
    ctx = ctx_factory(SessionState())
    ctx.warning = _warning_recorder(warnings)

    result = await auth_tool.authenticate(
        ctx,
        environment="cpd",
        region_or_url="https://cpd.example.com",
        apikey="platform-key",
        username="alice",
    )
    assert result["tls_verify"] is True
    assert warnings == []
