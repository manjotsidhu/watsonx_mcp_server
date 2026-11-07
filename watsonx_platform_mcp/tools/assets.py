"""Assets — see spec/02-tools.md #4-6.

list_assets/get_asset/create_asset (generic asset_type case): Phase 1,
high-confidence, live-verified. upload_asset_file: Phase 3, experimental —
only the create-data-asset step is confirmed; the
attachment/signed-URL/complete sequence is unconfirmed.
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Optional

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


@mcp.tool(
    annotations=ToolAnnotations(title="Upload Asset File", destructiveHint=False),
)
@redacted
async def upload_asset_file(
    ctx: Context,
    project_id: str,
    name: str,
    file_path: Annotated[Optional[str], Field(default=None, description="Local file path. Provide this or content_base64.")] = None,
    content_base64: Annotated[Optional[str], Field(default=None, description="Base64-encoded file content. Provide this or file_path.")] = None,
    mime_type: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """EXPERIMENTAL — Phase 3 / low-confidence (spec/02-tools.md).

    Only the create-data-asset step is verified live. The
    attachment-creation body shape is from a real example request, not
    independently re-verified. The signed-URL PUT and "complete" steps are
    entirely unconfirmed — this implementation is a best-effort, defensive
    sequence that should be re-verified against a live cluster before being
    relied on. If it fails partway through, the data asset (and possibly a
    dangling attachment record) may be left behind — this tool does not
    attempt rollback.
    """
    if not file_path and not content_base64:
        raise PlatformError("invalid_request", "Either file_path or content_base64 is required.", False)

    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    mime = mime_type or guess_mime_type(name)

    # Step 1 (confirmed live): create the data asset.
    create_body = {
        "metadata": {"name": name, "asset_type": "data_asset", "origin_country": "us"},
        "entity": {"data_asset": {"dataset": False, "mime_type": mime}},
    }
    if description is not None:
        create_body["metadata"]["description"] = description
    asset_resp = await client.request("POST", "/v2/assets", params={"project_id": project_id}, json_body=create_body)
    asset_resp = asset_resp or {}
    asset_id = asset_resp.get("asset_id") or extract_id(asset_resp)
    if not asset_id:
        raise PlatformError("upstream_error", "Asset creation succeeded but no asset_id was returned.", False)

    # Step 2 (from an example request, not independently re-verified live): create the attachment.
    object_key = f"/data_asset/{name}"
    attach_body = {
        "asset_type": "data_asset",
        "name": "referenced",
        "description": description or "",
        "object_key": object_key,
        "object_key_is_read_only": False,
        "is_partitioned": False,
    }
    attach_resp = await client.request(
        "POST", f"/v2/assets/{asset_id}/attachments", params={"project_id": project_id}, json_body=attach_body
    )
    attach_resp = attach_resp or {}
    attachment_id = attach_resp.get("attachment_id") or extract_id(attach_resp)

    # Step 3: TODO(spec/02-tools.md, UNCONFIRMED) — whether a signed upload URL is
    # returned for the file bytes, or the PUT target is derived some other way.
    # Defensive best-effort: look for a plausible URL field; fall back to PUTting
    # the attachment endpoint itself.
    upload_url = attach_resp.get("url") or attach_resp.get("upload_url") or attach_resp.get("signed_url")

    if file_path:
        data = read_local_file(file_path)
    else:
        data = base64.b64decode(content_base64)  # type: ignore[arg-type]

    if upload_url:
        await client.raw_put(upload_url, data)
    else:
        # TODO(spec/02-tools.md, UNCONFIRMED): no signed URL was returned; this
        # fallback (PUT straight to the attachment endpoint) is not described in
        # the spec at all and is only a guess pending live re-verification.
        await client.request(
            "PUT",
            f"/v2/assets/{asset_id}/attachments/{attachment_id}",
            params={"project_id": project_id},
            json_body=None,
        )

    # Step 4: TODO(spec/02-tools.md, UNCONFIRMED) — whether a "complete" call is
    # needed at all. Attempt it defensively; ignore a 404 (endpoint may not exist).
    try:
        await client.request(
            "POST",
            f"/v2/assets/{asset_id}/attachments/{attachment_id}/complete",
            params={"project_id": project_id},
            json_body={},
        )
    except PlatformError as exc:
        if exc.http_status != 404:
            raise

    return {"asset_id": asset_id, "attachment_id": attachment_id, "name": name}
