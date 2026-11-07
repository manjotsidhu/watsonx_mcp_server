"""Project members — see spec/02-tools.md #9.

EXPERIMENTAL — Phase 3. Zero testing was done on this entire capability
during spec validation. Implemented from the original 02-tools.md contract
as-is; treat every field name and endpoint here as an unverified assumption,
not fact.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..app import mcp
from ..bootstrap import ensure_authenticated
from ..http import PlatformClient
from ..pagination import clamp_limit, first_present_list
from ..utils import require_confirm
from . import redacted


def _normalize_member(resource: dict) -> dict:
    return {
        "user_id_or_iam_id": resource.get("user_iam_id") or resource.get("user_name") or resource.get("id"),
        "role": resource.get("role"),
        "type": resource.get("type"),
    }


@mcp.tool(annotations=ToolAnnotations(title="List Project Members", readOnlyHint=True))
@redacted
async def list_project_members(
    ctx: Context,
    project_id: str,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> dict:
    """EXPERIMENTAL — untested. Wraps GET /v2/projects/{id}/members."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    limit = clamp_limit(limit)

    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["bookmark"] = cursor
    resp = await client.request("GET", f"/v2/projects/{project_id}/members", params=params)
    resources = first_present_list(resp, keys=("members", "resources", "results"))
    items = [_normalize_member(r) for r in resources]

    bookmark = (resp or {}).get("bookmark")
    next_cursor = bookmark if bookmark and len(resources) >= limit else None
    return {"items": items, "next_cursor": next_cursor}


@mcp.tool(annotations=ToolAnnotations(title="Add Project Member", destructiveHint=False))
@redacted
async def add_project_member(
    ctx: Context,
    project_id: str,
    identity: str,
    role: Literal["admin", "editor", "viewer"],
) -> dict:
    """EXPERIMENTAL — untested. Wraps POST /v2/projects/{id}/members. `identity`
    is forwarded as-is (IAM id / username / service-id) — the exact expected
    request field name isn't confirmed; sent under a generically-named key."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    body = {"members": [{"user_iam_id": identity, "role": role}]}
    await client.request("POST", f"/v2/projects/{project_id}/members", json_body=body)
    return {"added": True, "identity": identity, "role": role}


@mcp.tool(annotations=ToolAnnotations(title="Update Project Member", idempotentHint=True))
@redacted
async def update_project_member(
    ctx: Context,
    project_id: str,
    identity: str,
    role: Literal["admin", "editor", "viewer"],
) -> dict:
    """EXPERIMENTAL — untested. Wraps PATCH /v2/projects/{id}/members/{member_id}.
    NOTE (unverified assumption): 02-tools.md's contract takes `identity` as input
    but the endpoint path uses `{member_id}` — no separate identity-to-member_id
    lookup is documented anywhere, so `identity` is used directly as the
    member_id path segment. This may be wrong; re-verify live."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request(
        "PATCH", f"/v2/projects/{project_id}/members/{identity}", json_body={"role": role}
    )
    if resp is None:
        resp = {"identity": identity, "role": role}
    return resp


@mcp.tool(annotations=ToolAnnotations(title="Remove Project Member", destructiveHint=True, idempotentHint=True))
@redacted
async def remove_project_member(ctx: Context, project_id: str, identity: str, confirm: bool = False) -> dict:
    """EXPERIMENTAL — untested. Wraps DELETE /v2/projects/{id}/members/{member_id}.
    Same unverified identity-as-member_id assumption as update_project_member."""
    require_confirm(confirm, "remove this project member")
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    await client.request("DELETE", f"/v2/projects/{project_id}/members/{identity}")
    return {"removed": True}
