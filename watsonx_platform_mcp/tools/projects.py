"""Projects — see spec/02-tools.md #2-3, #8. Phase 1: high-confidence, live-verified."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, List, Literal, Optional

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..bootstrap import ensure_authenticated
from ..errors import PlatformError
from ..http import PlatformClient
from ..pagination import bookmark_next_cursor, clamp_limit
from ..utils import extract_id, require_confirm
from . import redacted

_PROJECT_TYPES = ("cpd", "wx", "wca", "dpx", "wxbi")


def _normalize_project(resource: dict) -> dict:
    metadata = resource.get("metadata", {}) or {}
    entity = resource.get("entity", {}) or {}
    return {
        "id": metadata.get("guid"),
        "name": entity.get("name"),
        "description": entity.get("description"),
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "type": entity.get("type"),
        "creator": entity.get("creator"),
        "public": entity.get("public"),
    }


@mcp.tool(annotations=ToolAnnotations(title="List Projects", readOnlyHint=True))
@redacted
async def list_projects(
    ctx: Context,
    name_contains: Optional[str] = None,
    type: Annotated[
        Optional[List[Literal["cpd", "wx", "wca", "dpx", "wxbi"]]],
        Field(default=None, description="Server-side project-context filter, confirmed live on both SaaS and CPD."),
    ] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> dict:
    """List projects, flattened to {id, name, description, created_at, updated_at, type, creator, public}."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    limit = clamp_limit(limit)

    params: dict[str, Any] = {"limit": limit}
    if type:
        params["type"] = ",".join(type)
    if cursor:
        params["bookmark"] = cursor

    resp = await client.request("GET", "/v2/projects", params=params)
    resources = (resp or {}).get("resources", [])
    items = [_normalize_project(r) for r in resources]

    # NOTE: no confirmed server-side `name` filter exists for this endpoint —
    # applied client-side against the current page only (see 02-tools.md).
    if name_contains:
        needle = name_contains.lower()
        items = [it for it in items if it.get("name") and needle in it["name"].lower()]

    next_cursor = bookmark_next_cursor(resources, (resp or {}).get("bookmark"), limit)
    return {"items": items, "next_cursor": next_cursor}


@mcp.tool(annotations=ToolAnnotations(title="Get Project", readOnlyHint=True))
@redacted
async def get_project(ctx: Context, project_id: str) -> dict:
    """Full project document. Redacted: entity.storage.properties.credentials and
    similarly-named secret fields are stripped — confirmed live that SaaS can embed
    real COS/S3 credentials here (see spec/03-conventions.md)."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", f"/v2/projects/{project_id}")
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Create Project", destructiveHint=False))
@redacted
async def create_project(
    ctx: Context,
    name: str,
    description: Optional[str] = None,
    generator: Annotated[
        Optional[str], Field(default=None, description='Free-text product-origin marker, e.g. "CPDCTL-CLI".')
    ] = None,
    public: Optional[bool] = None,
    type: Annotated[Optional[str], Field(default=None, description="Same enum list_projects filters on, e.g. 'cpd'.")] = None,
    storage: Annotated[
        Optional[dict],
        Field(
            default=None,
            description=(
                "Environment-specific, opaque pass-through (see spec/04-environment-differences.md). "
                "On a `cpd` session, omit this and a {'type': 'assetfiles', 'guid': <uuid4>} is auto-filled "
                "— the platform ignores the client-supplied guid and provisions its own. On a `saas` "
                "session this is REQUIRED and cannot be auto-filled: "
                "{'type': 'bmcos_object_storage', 'guid': <COS instance guid>, "
                "'resource_crn': <real pre-existing COS instance CRN>, "
                "'properties': {'bucket_name': <fresh unique name>, 'bucket_region': ..., 'endpoint_url': ...}}. "
                "SaaS calls IBM Cloud's resource controller live against resource_crn and fails hard if it "
                "doesn't resolve to a real, accessible COS instance."
            ),
        ),
    ] = None,
) -> dict:
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    if storage is None:
        if state.environment == "cpd":
            storage = {"type": "assetfiles", "guid": str(uuid.uuid4())}
        else:
            raise PlatformError(
                "invalid_request",
                "`storage` is required on saas sessions — a real, pre-existing COS instance CRN "
                "is needed; there is no client-side shortcut on SaaS (see spec/04-environment-differences.md).",
                False,
            )

    body: dict[str, Any] = {"name": name, "storage": storage}
    if description is not None:
        body["description"] = description
    if generator is not None:
        body["generator"] = generator
    if public is not None:
        body["public"] = public
    if type is not None:
        body["type"] = type

    resp = await client.request("POST", "/transactional/v2/projects", json_body=body)
    project_id = extract_id(resp)
    return {"id": project_id, "name": name, "created_at": None}


@mcp.tool(annotations=ToolAnnotations(title="Update Project", idempotentHint=True))
@redacted
async def update_project(
    ctx: Context,
    project_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """PATCH with a flat, partial JSON object — confirmed live. NOTE: this PATCH
    body format is specific to projects; update_job needs a JSON Patch array
    instead (see spec/03-conventions.md)."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if not body:
        raise PlatformError("invalid_request", "At least one of name/description must be provided.", False)

    resp = await client.request("PATCH", f"/v2/projects/{project_id}", json_body=body)
    if resp is None:
        resp = await client.request("GET", f"/v2/projects/{project_id}")
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Delete Project", destructiveHint=True, idempotentHint=True))
@redacted
async def delete_project(ctx: Context, project_id: str, confirm: bool = False) -> dict:
    """Confirmed live: 204, immediate, cascades to the project's assets/jobs
    (not independently verified whether it cascades to connections/members)."""
    require_confirm(confirm, "delete this project")
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    await client.request("DELETE", f"/v2/projects/{project_id}")
    return {"deleted": True}
