"""Projects tools against the exact captured shapes in spec/02-tools.md and
spec/04-environment-differences.md."""

from __future__ import annotations

import uuid

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.session import SessionState
from watsonx_platform_mcp.tools import projects

# Real captured native envelope, spec/02-tools.md #2-3.
_LIST_PROJECTS_BODY = {
    "total_results": 1,
    "bookmark": "",
    "resources": [
        {
            "metadata": {"guid": "22222222-2222-4222-8222-222222222222", "url": "/v2/projects/22222222", "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-02T00:00:00Z"},
            "entity": {
                "name": "my-project",
                "type": "cpd",
                "generator": "CPDCTL-CLI",
                "public": False,
                "creator": "admin",
                "creator_iam_id": "iam-ServiceId-1",
                "storage": {"type": "assetfiles", "guid": "33333333-3333-4333-8333-333333333333"},
                "scope": {},
                "catalog": {},
            },
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_list_projects_flattens_native_envelope(cpd_zen_state: SessionState, ctx_factory):
    respx.get("https://cpd.example.com/v2/projects").mock(return_value=Response(200, json=_LIST_PROJECTS_BODY))
    ctx = ctx_factory(cpd_zen_state)
    result = await projects.list_projects(ctx)
    assert result["items"] == [
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "name": "my-project",
            "description": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "type": "cpd",
            "creator": "admin",
            "public": False,
        }
    ]
    assert result["next_cursor"] is None  # short page (1 < default limit 50)


@pytest.mark.asyncio
@respx.mock
async def test_list_projects_type_filter_sent_as_csv_query_param(cpd_zen_state: SessionState, ctx_factory):
    route = respx.get("https://cpd.example.com/v2/projects").mock(return_value=Response(200, json={"resources": [], "bookmark": ""}))
    ctx = ctx_factory(cpd_zen_state)
    await projects.list_projects(ctx, type=["wx", "dpx"])
    assert route.calls[0].request.url.params["type"] == "wx,dpx"


@pytest.mark.asyncio
@respx.mock
async def test_list_projects_invalid_type_400_propagates(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: invalid `type` value → 400 with a specific reason message."""
    body = {
        "id": "WSCPA0000E",
        "code": 400,
        "reason": "Failed to parse request parameters: type query parameter value is invalid: bogus-garbage-value. Expected one of: cpd, wx, wca, dpx, wxbi.",
        "message": "Bad Request",
    }
    respx.get("https://cpd.example.com/v2/projects").mock(return_value=Response(400, json=body))
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await projects.list_projects(ctx, type=["wx"])
    assert exc_info.value.code == "invalid_request"
    assert "bogus-garbage-value" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_get_project_returns_full_document_redacted(cpd_zen_state: SessionState, ctx_factory):
    doc = {
        "metadata": {"guid": "p1"},
        "entity": {
            "name": "proj",
            "storage": {"type": "bmcos_object_storage", "properties": {"credentials": {"api_key": "LEAKED"}}},
        },
    }
    respx.get("https://cpd.example.com/v2/projects/p1").mock(return_value=Response(200, json=doc))
    ctx = ctx_factory(cpd_zen_state)
    result = await projects.get_project(ctx, "p1")
    assert result["entity"]["storage"]["properties"]["credentials"] == "***REDACTED***"
    assert result["entity"]["name"] == "proj"


@pytest.mark.asyncio
@respx.mock
async def test_create_project_cpd_autofills_storage_guid(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: create_project on CPD auto-fills storage with a fresh
    uuid4 guid when the caller omits it; response is {"location": "/v2/projects/{id}"}."""
    route = respx.post("https://cpd.example.com/transactional/v2/projects").mock(
        return_value=Response(201, json={"location": "/v2/projects/22222222-2222-4222-8222-222222222222"})
    )
    ctx = ctx_factory(cpd_zen_state)
    result = await projects.create_project(ctx, name="new-project")

    assert result["id"] == "22222222-2222-4222-8222-222222222222"
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["storage"]["type"] == "assetfiles"
    # Client-minted guid must be a valid uuid4, freshly generated (not hardcoded).
    uuid.UUID(body["storage"]["guid"], version=4)


@pytest.mark.asyncio
async def test_create_project_saas_requires_storage_explicitly(saas_iam_state: SessionState, ctx_factory):
    """spec/04-environment-differences.md: no client-side shortcut on SaaS."""
    ctx = ctx_factory(saas_iam_state)
    with pytest.raises(PlatformError) as exc_info:
        await projects.create_project(ctx, name="new-project")
    assert exc_info.value.code == "invalid_request"
    assert "storage" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_create_project_saas_with_explicit_storage_succeeds(saas_iam_state: SessionState, ctx_factory):
    route = respx.post("https://api.dataplatform.cloud.ibm.com/transactional/v2/projects").mock(
        return_value=Response(201, json={"location": "/v2/projects/new-id"})
    )
    ctx = ctx_factory(saas_iam_state)
    storage = {
        "type": "bmcos_object_storage",
        "guid": "cos-guid",
        "resource_crn": "crn:v1:bluemix:public:cloud-object-storage:global:a/acct::instance::",
        "properties": {"bucket_name": "fresh-bucket", "bucket_region": "us-south", "endpoint_url": "https://s3.us-south.cloud-object-storage.appdomain.cloud"},
    }
    result = await projects.create_project(ctx, name="new-project", storage=storage)
    assert result["id"] == "new-id"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_update_project_sends_flat_partial_object(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: flat partial object, NOT a JSON Patch array (contrast update_job)."""
    route = respx.patch("https://cpd.example.com/v2/projects/p1").mock(return_value=Response(200, json={"entity": {"description": "new desc"}}))
    ctx = ctx_factory(cpd_zen_state)
    await projects.update_project(ctx, "p1", description="new desc")
    import json

    body = json.loads(route.calls[0].request.content)
    assert body == {"description": "new desc"}


@pytest.mark.asyncio
async def test_update_project_requires_at_least_one_field(cpd_zen_state: SessionState, ctx_factory):
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError):
        await projects.update_project(ctx, "p1")


@pytest.mark.asyncio
@respx.mock
async def test_delete_project_requires_confirm(cpd_zen_state: SessionState, ctx_factory):
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError) as exc_info:
        await projects.delete_project(ctx, "p1", confirm=False)
    assert exc_info.value.code == "invalid_request"


@pytest.mark.asyncio
@respx.mock
async def test_delete_project_confirmed_returns_deleted_true(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: 204, immediate."""
    respx.delete("https://cpd.example.com/v2/projects/p1").mock(return_value=Response(204))
    ctx = ctx_factory(cpd_zen_state)
    result = await projects.delete_project(ctx, "p1", confirm=True)
    assert result == {"deleted": True}
