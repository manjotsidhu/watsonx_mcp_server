"""Assets tools against the exact captured shapes in spec/02-tools.md."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.session import SessionState
from watsonx_platform_mcp.tools import assets


@pytest.mark.asyncio
@respx.mock
async def test_list_assets_uses_confirmed_envelope(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live envelope: {total_rows, results: []} — distinct from projects'."""
    body = {
        "total_rows": 1,
        "results": [{"metadata": {"asset_id": "a1", "name": "script.py", "asset_type": "script", "created_at": "2024-01-01T00:00:00Z"}}],
    }
    route = respx.post("https://cpd.example.com/v2/asset_types/asset/search").mock(return_value=Response(200, json=body))
    ctx = ctx_factory(cpd_zen_state)
    result = await assets.list_assets(ctx, project_id="proj-1")

    assert result["items"] == [{"asset_id": "a1", "name": "script.py", "asset_type": "script", "created_at": "2024-01-01T00:00:00Z"}]
    assert result["next_cursor"] is None
    assert route.calls[0].request.url.params["project_id"] == "proj-1"


@pytest.mark.asyncio
@respx.mock
async def test_list_assets_next_object_is_echoed_verbatim_as_cursor(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: the entire `next` object (not a single bookmark token)
    must be echoed back as the next page's request body."""
    next_obj = {"query": "*:*", "limit": 50, "bookmark": "abc", "skip": 50, "include_total_rows": True}
    first_page = {"total_rows": 100, "results": [{"metadata": {"asset_id": f"a{i}"}} for i in range(50)], "next": next_obj}
    route = respx.post("https://cpd.example.com/v2/asset_types/asset/search").mock(return_value=Response(200, json=first_page))
    ctx = ctx_factory(cpd_zen_state)

    result = await assets.list_assets(ctx, project_id="proj-1")
    assert result["next_cursor"] is not None

    from watsonx_platform_mcp.pagination import decode_json_cursor

    assert decode_json_cursor(result["next_cursor"]) == {**next_obj, "limit": 50}

    # Paging forward with that cursor replays the object verbatim as the body.
    await assets.list_assets(ctx, project_id="proj-1", cursor=result["next_cursor"])
    second_call_body = json.loads(route.calls[1].request.content)
    assert second_call_body["bookmark"] == "abc"
    assert second_call_body["skip"] == 50


@pytest.mark.asyncio
@respx.mock
async def test_get_asset_returns_full_document(cpd_zen_state: SessionState, ctx_factory):
    doc = {"metadata": {"asset_id": "a1"}, "entity": {}}
    respx.get("https://cpd.example.com/v2/assets/a1").mock(return_value=Response(200, json=doc))
    ctx = ctx_factory(cpd_zen_state)
    result = await assets.get_asset(ctx, project_id="proj-1", asset_id="a1")
    assert result == doc


@pytest.mark.asyncio
@respx.mock
async def test_create_asset_confirmed_envelope(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: request {"metadata": {...}, "entity": {}}; response has a
    top-level `asset_id` directly, no defensive extraction needed."""
    response_body = {
        "metadata": {"asset_id": "a2", "project_id": "proj-1", "catalog_id": "c1", "created_at": "2024-01-01T00:00:00Z", "owner_id": "o1"},
        "entity": {},
        "href": "/v2/assets/a2?project_id=proj-1",
        "asset_id": "a2",
    }
    route = respx.post("https://cpd.example.com/v2/assets").mock(return_value=Response(201, json=response_body))
    ctx = ctx_factory(cpd_zen_state)
    result = await assets.create_asset(ctx, project_id="proj-1", name="my-script", asset_type="script")

    assert result == {"asset_id": "a2", "name": "my-script", "asset_type": "script", "created_at": "2024-01-01T00:00:00Z"}
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body == {"metadata": {"name": "my-script", "asset_type": "script", "origin_country": "us"}, "entity": {}}


@pytest.mark.asyncio
@respx.mock
async def test_create_asset_data_asset_entity_shape_from_example_request(cpd_zen_state: SessionState, ctx_factory):
    """From a real example request, not independently re-verified live (spec/02-tools.md)."""
    route = respx.post("https://cpd.example.com/v2/assets").mock(
        return_value=Response(201, json={"asset_id": "a3", "metadata": {"created_at": "t"}})
    )
    ctx = ctx_factory(cpd_zen_state)
    await assets.create_asset(
        ctx,
        project_id="proj-1",
        name="wordcount2.py",
        asset_type="data_asset",
        attributes={"data_asset": {"dataset": False, "mime_type": "text/x-script.python"}},
    )
    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["entity"] == {"data_asset": {"dataset": False, "mime_type": "text/x-script.python"}}
