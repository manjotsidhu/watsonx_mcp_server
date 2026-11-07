"""Connections — see spec/02-tools.md #7.

list_connection_types/create_connection/list_connections/get_connection: Phase 2,
confirmed live, happy path only. update_connection/delete_connection: Phase 3 —
never tested at all (create_connection needs real reachable external credentials
to produce something to update/delete against).
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..bootstrap import ensure_authenticated
from ..http import PlatformClient
from ..pagination import clamp_limit, href_next_cursor, split_href
from ..utils import extract_id, require_confirm
from . import redacted


def _normalize_connection_type(entry: dict) -> dict:
    entity = entry.get("entity", entry) or {}
    metadata = entry.get("metadata", {}) or {}
    return {
        "datasource_type_id": metadata.get("asset_id") or entry.get("datasource_type_id") or entity.get("id"),
        "name": entity.get("name"),
        "label": entity.get("label"),
        "required_properties": entity.get("properties") or entity.get("required_properties") or [],
    }


def _normalize_connection(resource: dict) -> dict:
    metadata = resource.get("metadata", {}) or {}
    entity = resource.get("entity", {}) or {}
    return {
        "connection_id": metadata.get("asset_id") or metadata.get("guid"),
        "name": entity.get("name"),
        "datasource_type": entity.get("datasource_type"),
        "description": entity.get("description"),
        "properties": entity.get("properties"),
    }


@mcp.tool(annotations=ToolAnnotations(title="List Connection Types", readOnlyHint=True))
@redacted
async def list_connection_types(ctx: Context, name_contains: Optional[str] = None) -> dict:
    """Wraps GET /v2/datasource_types."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", "/v2/datasource_types")
    resources = (resp or {}).get("resources", (resp or {}).get("datasource_types", []))
    items = [_normalize_connection_type(r) for r in resources]
    if name_contains:
        needle = name_contains.lower()
        items = [it for it in items if it.get("name") and needle in it["name"].lower()]
    return {"items": items}


@mcp.tool(annotations=ToolAnnotations(title="Create Connection", destructiveHint=False))
@redacted
async def create_connection(
    ctx: Context,
    project_id: str,
    name: str,
    datasource_type: Annotated[
        str, Field(description="e.g. 's3'/'cos'/'bluemixcloudobjectstorage', or a datasource_types asset guid.")
    ],
    properties: Annotated[
        dict,
        Field(description="Opaque per-datasource-type payload: bucket, endpoint, credentials, auth_method, etc."),
    ],
    description: Optional[str] = None,
) -> dict:
    """IMPORTANT: this call actively tests the supplied credentials against the
    live external system at creation time and fails with a non-standard HTTP 460
    ("connection_test_failed", non-retryable) if they don't work — confirmed live.
    It cannot be used speculatively or with placeholder credentials; only call it
    with genuinely reachable external-system credentials. `properties` may
    contain secrets (e.g. an access key) — they are forwarded to the API but
    never echoed back in this tool's output."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    body: dict[str, Any] = {"name": name, "datasource_type": datasource_type, "properties": properties}
    if description is not None:
        body["description"] = description

    resp = await client.request("POST", "/v2/connections", params={"project_id": project_id}, json_body=body)
    resp = resp or {}
    connection_id = extract_id(resp) or (resp.get("metadata") or {}).get("asset_id")
    return {"connection_id": connection_id, "name": name, "datasource_type": datasource_type}


@mcp.tool(annotations=ToolAnnotations(title="List Connections", readOnlyHint=True))
@redacted
async def list_connections(
    ctx: Context,
    project_id: str,
    name_contains: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> dict:
    """Native envelope confirmed live: {resources: [], first: {href}, total_count}
    — a third, HAL-style pagination shape distinct from projects and asset search."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    limit = clamp_limit(limit)

    if cursor:
        # cursor is a previously-observed href — replay it verbatim.
        # TODO(spec/02-tools.md, UNCONFIRMED): no `next` link was actually observed
        # live for this endpoint, only `first.href` — this path is speculative.
        path, params = split_href(cursor, state.base_url or "")
        resp = await client.request("GET", path, params=params)
    else:
        resp = await client.request("GET", "/v2/connections", params={"project_id": project_id, "limit": limit})

    resp = resp or {}
    resources = resp.get("resources", [])
    items = [_normalize_connection(r) for r in resources]
    next_cursor = href_next_cursor(resp)
    return {"items": items, "next_cursor": next_cursor}


@mcp.tool(annotations=ToolAnnotations(title="Get Connection", readOnlyHint=True))
@redacted
async def get_connection(ctx: Context, project_id: str, connection_id: str) -> dict:
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", f"/v2/connections/{connection_id}", params={"project_id": project_id})
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Update Connection", idempotentHint=True))
@redacted
async def update_connection(
    ctx: Context,
    project_id: str,
    connection_id: str,
    name: Optional[str] = None,
    properties: Optional[dict] = None,
    description: Optional[str] = None,
) -> dict:
    """EXPERIMENTAL — Phase 3, never tested at all (spec/02-tools.md). Assumes the
    same flat partial-object PATCH body as update_project; this is an *assumption*,
    not a confirmed fact — update_connection's PATCH format could just as easily
    match update_job's JSON-Patch-array format instead. Re-verify live once real
    external-system credentials are available to create a connection to test against."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if properties is not None:
        body["properties"] = properties
    if description is not None:
        body["description"] = description

    resp = await client.request("PATCH", f"/v2/connections/{connection_id}", params={"project_id": project_id}, json_body=body)
    if resp is None:
        resp = await client.request("GET", f"/v2/connections/{connection_id}", params={"project_id": project_id})
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Delete Connection", destructiveHint=True, idempotentHint=True))
@redacted
async def delete_connection(ctx: Context, project_id: str, connection_id: str, confirm: bool = False) -> dict:
    """EXPERIMENTAL — Phase 3, never tested at all (spec/02-tools.md)."""
    require_confirm(confirm, "delete this connection")
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    await client.request("DELETE", f"/v2/connections/{connection_id}", params={"project_id": project_id})
    return {"deleted": True}
