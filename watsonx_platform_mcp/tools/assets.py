"""Assets — see spec/02-tools.md #4-6.

list_assets/get_asset/create_asset (generic asset_type case): Phase 1,
high-confidence, live-verified. upload_asset_file: file-first multipart PUT,
then asset + referenced attachment — live-verified on CPD, untested on SaaS.
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Optional
from urllib.parse import quote

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..bootstrap import ensure_authenticated
from ..errors import PlatformError
from ..http import PlatformClient
from ..pagination import clamp_limit, decode_json_cursor, encode_json_cursor
from ..utils import extract_id, guess_mime_type, read_local_file
from . import redacted


def _normalize_asset(result: dict) -> dict:
    metadata = result.get("metadata", {}) or result
    return {
        "asset_id": metadata.get("asset_id"),
        "name": metadata.get("name"),
        "asset_type": metadata.get("asset_type"),
        "created_at": metadata.get("created_at"),
    }


@mcp.tool(annotations=ToolAnnotations(title="List Assets", readOnlyHint=True))
@redacted
async def list_assets(
    ctx: Context,
    project_id: str,
    asset_type: Optional[str] = None,
    name_contains: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> dict:
    """Wraps POST /v2/asset_types/asset/search — envelope {total_rows, results}
    confirmed live. NOTE: the exact query-DSL field names used below
    (asset.asset_type / asset.name) are inferred from the general watsonx asset
    search pattern, not independently confirmed in the spec — only the response
    envelope shape itself is confirmed. Re-verify the filter syntax against a
    live cluster with real data if filtered results look wrong.
    """
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    limit = clamp_limit(limit)

    if cursor:
        # The confirmed pagination contract: the entire native `next` object
        # must be echoed back verbatim as the next request's body.
        body = decode_json_cursor(cursor)
        body["limit"] = limit
    else:
        query_parts = []
        if asset_type:
            query_parts.append(f"asset.asset_type:{asset_type}")
        if name_contains:
            query_parts.append(f"asset.name:*{name_contains}*")
        body = {"query": " AND ".join(query_parts) if query_parts else "*:*", "limit": limit}

    resp = await client.request("POST", "/v2/asset_types/asset/search", params={"project_id": project_id}, json_body=body)
    results = (resp or {}).get("results", [])
    items = [_normalize_asset(r) for r in results]

    next_obj = (resp or {}).get("next")
    next_cursor = encode_json_cursor(next_obj) if next_obj else None
    return {"items": items, "next_cursor": next_cursor}


@mcp.tool(annotations=ToolAnnotations(title="Get Asset", readOnlyHint=True))
@redacted
async def get_asset(ctx: Context, project_id: str, asset_id: str) -> dict:
    """Full asset metadata document (attachments, attributes, relationships)."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", f"/v2/assets/{asset_id}", params={"project_id": project_id})
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Create Asset", destructiveHint=False))
@redacted
async def create_asset(
    ctx: Context,
    project_id: str,
    name: str,
    asset_type: Annotated[str, Field(description="e.g. 'data_asset', 'script', or a product-defined type.")],
    description: Optional[str] = None,
    attributes: Annotated[
        Optional[dict],
        Field(default=None, description="Opaque per-type payload, becomes the asset's `entity` content. Empty for a bare data_asset."),
    ] = None,
) -> dict:
    """Generic asset creation — confirmed live at POST /v2/assets?project_id=...
    (not /transactional/v2/assets, which is 404). `asset_id` is present directly
    at the top level of the response, no defensive extraction needed for this one."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    metadata: dict[str, Any] = {"name": name, "asset_type": asset_type, "origin_country": "us"}
    if description is not None:
        metadata["description"] = description
    body = {"metadata": metadata, "entity": attributes or {}}

    resp = await client.request("POST", "/v2/assets", params={"project_id": project_id}, json_body=body)
    resp = resp or {}
    asset_id = resp.get("asset_id") or extract_id(resp)
    return {
        "asset_id": asset_id,
        "name": name,
        "asset_type": asset_type,
        "created_at": (resp.get("metadata") or {}).get("created_at"),
    }


def _safe_asset_file_name(name: str) -> str:
    """The name becomes part of the object key (/data_asset/<name>) and the PUT
    URL path, so it must be a single plain path segment."""
    bad = (
        not name
        or name != name.strip()
        or name in (".", "..")
        or any(c in name for c in "/\\?#%")
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or len(name) > 200
    )
    if bad:
        raise PlatformError(
            "invalid_request",
            "`name` must be a plain file name: no slashes, '?', '#', '%', control characters, "
            "leading/trailing spaces, or '.'/'..', and at most 200 characters.",
            False,
        )
    return name


@mcp.tool(
    annotations=ToolAnnotations(title="Upload Asset File", destructiveHint=False),
)
@redacted
async def upload_asset_file(
    ctx: Context,
    project_id: str,
    name: Annotated[str, Field(description="Plain file name (no slashes); becomes part of the object key /data_asset/<name>.")],
    file_path: Annotated[Optional[str], Field(default=None, description="Local file path. Provide this or content_base64.")] = None,
    content_base64: Annotated[Optional[str], Field(default=None, description="Base64-encoded file content. Provide this or file_path.")] = None,
    mime_type: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Create a data asset holding the given file content.

    Sequence (live-verified on CPD, spec/02-tools.md #6): (1) multipart PUT of
    the bytes to /v2/asset_files/data_asset/<name>, (2) create the data asset,
    (3) create a *referenced* attachment pointing at the uploaded object key.
    If step 2 or 3 fails the asset is deleted (best-effort rollback).
    SaaS: same sequence, NOT verified live (only CPD was).

    Uploading the same name twice: the object is overwritten and a second,
    separate asset is created that references the same object key (see spec).
    """
    if not file_path and not content_base64:
        raise PlatformError("invalid_request", "Either file_path or content_base64 is required.", False)
    name = _safe_asset_file_name(name)

    if file_path:
        data = read_local_file(file_path)
    else:
        try:
            data = base64.b64decode(content_base64, validate=True)  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            raise PlatformError("invalid_request", f"content_base64 is not valid base64: {exc}", False) from exc

    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    mime = mime_type or guess_mime_type(name)
    params = {"project_id": project_id}
    object_key = f"/data_asset/{name}"

    # Step 1: the bytes go first. `object_key` in an attachment means "reference
    # an existing object", so the object must exist before the attachment does.
    await client.put_multipart(
        f"/v2/asset_files/data_asset/{quote(name, safe='')}",
        file_name=name,
        content=data,
        mime_type=mime,
        params=params,
    )

    # Step 2: create the data asset.
    create_body = {
        "metadata": {"name": name, "asset_type": "data_asset", "origin_country": "us"},
        "entity": {"data_asset": {"dataset": False, "mime_type": mime}},
    }
    if description is not None:
        create_body["metadata"]["description"] = description
    asset_resp = await client.request("POST", "/v2/assets", params=params, json_body=create_body)
    asset_resp = asset_resp or {}
    asset_id = asset_resp.get("asset_id") or (asset_resp.get("metadata") or {}).get("asset_id") or extract_id(asset_resp)
    if not asset_id:
        raise PlatformError("upstream_error", "Asset creation succeeded but no asset_id was returned.", False)

    # Step 3: referenced attachment. No /complete call is needed in this form.
    try:
        attach_body = {
            "asset_type": "data_asset",
            "name": "referenced",
            "description": description or "",
            "object_key": object_key,
            "object_key_is_read_only": False,
            "is_partitioned": False,
        }
        attach_resp = await client.request(
            "POST", f"/v2/assets/{asset_id}/attachments", params=params, json_body=attach_body
        )
        attach_resp = attach_resp or {}
        attachment_id = attach_resp.get("attachment_id") or extract_id(attach_resp)
        if not attachment_id:
            raise PlatformError("upstream_error", "Attachment creation succeeded but no attachment_id was returned.", False)
    except BaseException:
        try:
            await client.request("DELETE", f"/v2/assets/{asset_id}", params=params)
        except PlatformError:
            pass  # rollback is best-effort; surface the original failure
        raise

    return {"asset_id": asset_id, "attachment_id": attachment_id, "name": name}
