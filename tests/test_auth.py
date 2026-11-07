"""Auth flows — spec/01-auth-and-environments.md. Response shapes are the
exact ones confirmed live in the spec."""

from __future__ import annotations

import base64

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.auth import authenticate_session
from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.session import SessionState


@pytest.mark.asyncio
async def test_cpd_zen_api_key_needs_no_network_call():
    state = SessionState()
    with respx.mock(assert_all_called=True) as router:
        # No routes registered — if ZenApiKey made any HTTP call, respx would
        # raise on the unmocked request.
        await authenticate_session(
            state,
            environment="cpd",
            region_or_url="https://cpd.example.com/",
            apikey="platform-key",
            username="alice",
        )
    assert state.auth_method == "zen_api_key"
    assert state.base_url == "https://cpd.example.com"  # trailing slash stripped
    assert state.expires_at is None
    expected = "ZenApiKey " + base64.b64encode(b"alice:platform-key").decode()
    assert state.zen_header == expected


@pytest.mark.asyncio
@respx.mock
async def test_cpd_bearer_fallback_uses_api_key_field_name():
    """Confirmed live: the field must be named `api_key`, not `apikey`."""
    route = respx.post("https://cpd.example.com/icp4d-api/v1/authorize").mock(
        return_value=Response(200, json={"_messageCode_": 200, "message": "Success", "token": "jwt-token"})
    )
    state = SessionState()
    await authenticate_session(
        state,
        environment="cpd",
        region_or_url="https://cpd.example.com",
        apikey="platform-key",
        username="alice",
        prefer_zen_api_key=False,
    )
    assert route.called
    sent_body = route.calls[0].request.content
    import json

    parsed = json.loads(sent_body)
    assert parsed == {"username": "alice", "api_key": "platform-key"}
    assert state.auth_method == "cpd_bearer"
    assert state.token == "jwt-token"
    assert state.expires_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_cpd_bearer_401_raises_normalized_error():
    respx.post("https://cpd.example.com/icp4d-api/v1/authorize").mock(
        return_value=Response(
            401, json={"_statusCode_": 401, "exception": "Sorry, we could not verify your credentials.", "message": "Unauthorized"}
        )
    )
    state = SessionState()
    with pytest.raises(PlatformError) as exc_info:
        await authenticate_session(
            state,
            environment="cpd",
            region_or_url="https://cpd.example.com",
            password="wrong",
            username="alice",
        )
    assert exc_info.value.code == "unauthorized"
    assert "could not verify" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_saas_iam_exchange_confirmed_shape():
    route = respx.post("https://iam.cloud.ibm.com/identity/token").mock(
        return_value=Response(
            200,
            json={
                "access_token": "eyJ.jwt.token",
                "refresh_token": "not_supported",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expiration": 1788624990,
                "scope": "ibm openid",
            },
        )
    )
    state = SessionState()
    await authenticate_session(
        state,
        environment="saas",
        region_or_url="us-south",
        apikey="my-ibm-cloud-apikey",
    )
    assert route.called
    sent = route.calls[0].request
    assert "apikey=my-ibm-cloud-apikey" in sent.content.decode()
    assert "grant_type=urn" in sent.content.decode()
    assert state.auth_method == "iam"
    assert state.token == "eyJ.jwt.token"
    assert state.base_url == "https://api.dataplatform.cloud.ibm.com"


@pytest.mark.asyncio
async def test_saas_missing_apikey_raises_before_any_network_call():
    state = SessionState()
    with respx.mock(assert_all_called=False):
        with pytest.raises(PlatformError) as exc_info:
            await authenticate_session(state, environment="saas", region_or_url="us-south")
    assert exc_info.value.code == "invalid_request"


@pytest.mark.asyncio
async def test_cpd_missing_username_raises():
    state = SessionState()
    with pytest.raises(PlatformError) as exc_info:
        await authenticate_session(state, environment="cpd", region_or_url="https://cpd.example.com", apikey="k")
    assert exc_info.value.code == "invalid_request"
