"""Connections tools — spec/02-tools.md #7. Includes the 460 credential-test finding."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.session import SessionState
from watsonx_platform_mcp.tools import connections


@pytest.mark.asyncio
@respx.mock
async def test_create_connection_invalid_datasource_type_error(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: invalid datasource_type gets a specific validation error."""
    body = {"errors": [{"code": "invalid_payload", "message": "A data source of the specified type [x] does not exist."}]}
    respx.post("https://cpd.example.com/v2/connections").mock(return_value=Response(400, json=body))
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await connections.create_connection(ctx, project_id="p1", name="conn", datasource_type="x", properties={})
    assert exc_info.value.code == "invalid_request"
    assert "does not exist" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_create_connection_missing_properties_error(cpd_zen_state: SessionState, ctx_factory):
    body = {
        "errors": [
            {"code": "invalid_payload", "message": "One of the properties [host, password, port, server, username] is required."}
        ]
    }
    respx.post("https://cpd.example.com/v2/connections").mock(return_value=Response(400, json=body))
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await connections.create_connection(ctx, project_id="p1", name="conn", datasource_type="informix", properties={})
    assert "host, password, port, server, username" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_create_connection_460_credential_test_failed(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: non-standard HTTP 460 when live credential test fails.
    Non-retryable per spec/03-conventions.md."""
    body = {
        "errors": [
            {
                "code": "invalid_data",
                "message": "The connection could not be established: CDICO0103E: [IBM Cloud Object Storage] Invalid username or password: ... InvalidAccessKeyId ...",
                "more_info": "Check the connection properties and ensure that the resource is accessible.",
            }
        ]
    }
    respx.post("https://cpd.example.com/v2/connections").mock(return_value=Response(460, json=body))
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await connections.create_connection(
            ctx,
            project_id="p1",
            name="cos-conn",
            datasource_type="bluemixcloudobjectstorage",
            properties={"access_key": "fake", "secret_key": "fake"},
        )
    assert exc_info.value.code == "connection_test_failed"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
@respx.mock
async def test_create_connection_never_echoes_secret_properties_back(cpd_zen_state: SessionState, ctx_factory):
    """Output redaction must cover connection properties, per spec/03-conventions.md."""
    response_body = {
        "metadata": {"asset_id": "conn-1"},
        "entity": {"name": "cos-conn", "datasource_type": "bluemixcloudobjectstorage", "properties": {"access_key": "AKIA-REAL", "secret_key": "REAL-SECRET"}},
    }
    respx.post("https://cpd.example.com/v2/connections").mock(return_value=Response(201, json=response_body))
    ctx = ctx_factory(cpd_zen_state)
    result = await connections.create_connection(
        ctx, project_id="p1", name="cos-conn", datasource_type="bluemixcloudobjectstorage", properties={"access_key": "AKIA-REAL", "secret_key": "REAL-SECRET"}
    )
    # The tool's own normalized output never includes `properties` at all.
    assert "properties" not in result
    assert result == {"connection_id": "conn-1", "name": "cos-conn", "datasource_type": "bluemixcloudobjectstorage"}


@pytest.mark.asyncio
@respx.mock
async def test_list_connections_hal_style_envelope(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: {resources: [], first: {href}, total_count} — a third
    distinct pagination shape."""
    body = {
        "resources": [{"metadata": {"asset_id": "c1"}, "entity": {"name": "conn1", "datasource_type": "s3", "properties": {"secret_key": "SECRET"}}}],
        "first": {"href": "/v2/connections?project_id=p1&limit=50"},
        "total_count": 1,
    }
    respx.get("https://cpd.example.com/v2/connections").mock(return_value=Response(200, json=body))
    ctx = ctx_factory(cpd_zen_state)
    result = await connections.list_connections(ctx, project_id="p1")
    assert result["items"][0]["connection_id"] == "c1"
    assert result["items"][0]["properties"]["secret_key"] == "***REDACTED***"
    assert result["next_cursor"] is None  # only `first` was observed live, no `next`


@pytest.mark.asyncio
@respx.mock
async def test_get_connection(cpd_zen_state: SessionState, ctx_factory):
    respx.get("https://cpd.example.com/v2/connections/c1").mock(return_value=Response(200, json={"metadata": {"asset_id": "c1"}}))
    ctx = ctx_factory(cpd_zen_state)
    result = await connections.get_connection(ctx, project_id="p1", connection_id="c1")
    assert result["metadata"]["asset_id"] == "c1"


@pytest.mark.asyncio
@respx.mock
async def test_delete_connection_requires_confirm(cpd_zen_state: SessionState, ctx_factory):
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError):
        await connections.delete_connection(ctx, project_id="p1", connection_id="c1", confirm=False)
