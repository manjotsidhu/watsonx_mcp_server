"""PlatformClient — one transparent 401 re-auth + retry, per spec/01-auth-and-environments.md."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.http import PlatformClient
from watsonx_platform_mcp.session import SessionState


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_one_reauth_then_retries_and_succeeds(saas_iam_state: SessionState):
    call_count = {"projects": 0}

    def projects_responder(request):
        call_count["projects"] += 1
        if call_count["projects"] == 1:
            return Response(401, json={"message": "token expired"})
        return Response(200, json={"total_results": 0, "bookmark": "", "resources": []})

    respx.get("https://api.dataplatform.cloud.ibm.com/v2/projects").mock(side_effect=projects_responder)
    respx.post("https://iam.cloud.ibm.com/identity/token").mock(
        return_value=Response(200, json={"access_token": "new-token", "expires_in": 3600})
    )

    client = PlatformClient(saas_iam_state)
    result = await client.request("GET", "/v2/projects")

    assert call_count["projects"] == 2
    assert result == {"total_results": 0, "bookmark": "", "resources": []}
    assert saas_iam_state.token == "new-token"


@pytest.mark.asyncio
@respx.mock
async def test_401_after_retry_still_fails_surfaces_error(saas_iam_state: SessionState):
    respx.get("https://api.dataplatform.cloud.ibm.com/v2/projects").mock(return_value=Response(401, json={"message": "still bad"}))
    respx.post("https://iam.cloud.ibm.com/identity/token").mock(
        return_value=Response(200, json={"access_token": "new-token-2", "expires_in": 3600})
    )

    client = PlatformClient(saas_iam_state)
    with pytest.raises(PlatformError) as exc_info:
        await client.request("GET", "/v2/projects")
    assert exc_info.value.code == "unauthorized"


@pytest.mark.asyncio
@respx.mock
async def test_zen_api_key_401_does_not_attempt_reauth(cpd_zen_state: SessionState):
    respx.get("https://cpd.example.com/v2/projects").mock(return_value=Response(401, json={"message": "nope"}))
    client = PlatformClient(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await client.request("GET", "/v2/projects")
    assert exc_info.value.code == "unauthorized"


@pytest.mark.asyncio
async def test_no_session_raises_not_authenticated():
    client = PlatformClient(SessionState())
    with pytest.raises(PlatformError) as exc_info:
        await client.request("GET", "/v2/projects")
    assert exc_info.value.code == "not_authenticated"


@pytest.mark.asyncio
@respx.mock
async def test_204_returns_none(cpd_zen_state: SessionState):
    respx.delete("https://cpd.example.com/v2/projects/p1").mock(return_value=Response(204))
    client = PlatformClient(cpd_zen_state)
    result = await client.request("DELETE", "/v2/projects/p1")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_460_propagates_as_connection_test_failed(cpd_zen_state: SessionState):
    body = {"errors": [{"code": "invalid_data", "message": "The connection could not be established: bad creds"}]}
    respx.post("https://cpd.example.com/v2/connections").mock(return_value=Response(460, json=body))
    client = PlatformClient(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await client.request("POST", "/v2/connections", json_body={})
    assert exc_info.value.code == "connection_test_failed"
    assert exc_info.value.retryable is False
