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


# --- upload_asset_file: file first, then asset, then referenced attachment ---

_B = "https://cpd.example.com"


def _mock_upload_chain(attach_response=None):
    put = respx.put(f"{_B}/v2/asset_files/data_asset/script.py").mock(
        return_value=Response(201, json={"status": "Asset created: The asset was successfully uploaded."})
    )
    create = respx.post(f"{_B}/v2/assets").mock(return_value=Response(201, json={"asset_id": "a1"}))
    attach = respx.post(f"{_B}/v2/assets/a1/attachments").mock(
        return_value=attach_response or Response(201, json={"attachment_id": "att-1", "url1": "/v2/asset_files/c/a1/att-1", "is_referenced": True})
    )
    return put, create, attach


@pytest.mark.asyncio
@respx.mock
async def test_upload_asset_file_sequence_cpd(cpd_zen_state: SessionState, ctx_factory):
    import base64

    put, create, attach = _mock_upload_chain()
    ctx = ctx_factory(cpd_zen_state)
    result = await assets.upload_asset_file(
        ctx, project_id="p1", name="script.py", content_base64=base64.b64encode(b"print('hi')").decode()
    )
    assert result == {"asset_id": "a1", "attachment_id": "att-1", "name": "script.py"}

    # File PUT first: multipart with a part named `file`, auth header, project_id.
    put_req = put.calls[0].request
    assert put_req.url.params["project_id"] == "p1"
    assert put_req.headers["authorization"] == "ZenApiKey dXNlcjprZXk="
    assert put_req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="script.py"' in put_req.content
    assert b"print('hi')" in put_req.content
    assert b"text/x-python" in put_req.content

    # Asset creation carries no object_key; attachment references the object.
    create_body = json.loads(create.calls[0].request.content)
    assert create_body["entity"]["data_asset"] == {"dataset": False, "mime_type": "text/x-python"}
    assert "object_key" not in json.dumps(create_body)
    assert json.loads(attach.calls[0].request.content) == {
        "asset_type": "data_asset",
        "name": "referenced",
        "description": "",
        "object_key": "/data_asset/script.py",
        "object_key_is_read_only": False,
        "is_partitioned": False,
    }
    # Order: PUT, asset, attachment; and no /complete call.
    assert len(respx.calls) == 3
    assert [c.request.method for c in respx.calls] == ["PUT", "POST", "POST"]


@pytest.mark.asyncio
@respx.mock
async def test_upload_asset_file_from_file_path(cpd_zen_state: SessionState, ctx_factory, tmp_path):
    f = tmp_path / "script.py"
    f.write_text("print('x')")
    put, _, _ = _mock_upload_chain()
    result = await assets.upload_asset_file(ctx_factory(cpd_zen_state), project_id="p1", name="script.py", file_path=str(f))
    assert result["asset_id"] == "a1"
    assert b"print('x')" in put.calls[0].request.content


@pytest.mark.asyncio
@respx.mock
async def test_upload_asset_file_rolls_back_asset_when_attachment_fails(cpd_zen_state: SessionState, ctx_factory):
    from watsonx_platform_mcp.errors import PlatformError

    _mock_upload_chain(attach_response=Response(500, json={"message": "boom"}))
    delete = respx.delete(f"{_B}/v2/assets/a1").mock(return_value=Response(204))
    with pytest.raises(PlatformError):
        await assets.upload_asset_file(ctx_factory(cpd_zen_state), project_id="p1", name="script.py", content_base64="eA==")
    assert delete.called
    assert delete.calls[0].request.url.params["project_id"] == "p1"


@pytest.mark.asyncio
@respx.mock
async def test_upload_asset_file_rollback_failure_surfaces_original_error(cpd_zen_state: SessionState, ctx_factory):
    from watsonx_platform_mcp.errors import PlatformError

    _mock_upload_chain(attach_response=Response(400, json={"message": "bad attachment"}))
    respx.delete(f"{_B}/v2/assets/a1").mock(return_value=Response(500))
    with pytest.raises(PlatformError) as ei:
        await assets.upload_asset_file(ctx_factory(cpd_zen_state), project_id="p1", name="script.py", content_base64="eA==")
    assert ei.value.http_status == 400


@pytest.mark.asyncio
@respx.mock
async def test_upload_asset_file_failed_put_creates_nothing(cpd_zen_state: SessionState, ctx_factory):
    from watsonx_platform_mcp.errors import PlatformError

    respx.put(f"{_B}/v2/asset_files/data_asset/script.py").mock(return_value=Response(400, json={"message": "Invalid content type"}))
    create = respx.post(f"{_B}/v2/assets").mock(return_value=Response(201, json={"asset_id": "a1"}))
    with pytest.raises(PlatformError):
        await assets.upload_asset_file(ctx_factory(cpd_zen_state), project_id="p1", name="script.py", content_base64="eA==")
    assert not create.called


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["../x.py", "a/b.py", "a\\b.py", "..", ".", "", " x.py", "x?.py", "x#.py", "x%2e.py", "x\n.py"])
async def test_upload_asset_file_rejects_unsafe_names(cpd_zen_state: SessionState, ctx_factory, bad):
    from watsonx_platform_mcp.errors import PlatformError

    with pytest.raises(PlatformError) as ei:
        await assets.upload_asset_file(ctx_factory(cpd_zen_state), project_id="p1", name=bad, content_base64="eA==")
    assert ei.value.code == "invalid_request"


@pytest.mark.asyncio
async def test_upload_asset_file_rejects_invalid_base64(cpd_zen_state: SessionState, ctx_factory):
    from watsonx_platform_mcp.errors import PlatformError

    with pytest.raises(PlatformError):
        await assets.upload_asset_file(ctx_factory(cpd_zen_state), project_id="p1", name="s.py", content_base64="not base64!!")
