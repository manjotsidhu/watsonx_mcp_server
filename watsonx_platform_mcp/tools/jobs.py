"""Jobs — see spec/02-tools.md #10-12.

create_job (asset_ref shape)/list_jobs/get_job/delete_job: Phase 2, confirmed
live, happy path only. update_job (schedule editing): Phase 3 — the JSON-Patch
format is confirmed but two live attempts to actually change a schedule value
did nothing.

Jobs are platform assets (asset_type: "job") — job_id is an asset_id, confirmed by
both the create response shape and by deleting a job via the plain
asset-delete endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..bootstrap import ensure_authenticated
from ..errors import PlatformError
from ..http import PlatformClient
from ..pagination import clamp_limit, first_present_list
from ..utils import extract_id, require_confirm, with_wxd_credentials
from . import redacted


def _normalize_job(resource: dict) -> dict:
    metadata = resource.get("metadata", {}) or resource
    entity = resource.get("entity", {}) or {}
    job = entity.get("job", {}) or {}
    return {
        "job_id": metadata.get("asset_id"),
        "name": metadata.get("name") or job.get("name"),
        "job_type": metadata.get("asset_type"),
        "schedule_enabled": bool(job.get("schedule")),
    }


@mcp.tool(annotations=ToolAnnotations(title="Create Job", destructiveHint=False))
@redacted
async def create_job(
    ctx: Context,
    project_id: str,
    name: str,
    job_type: Annotated[
        str,
        Field(
            description=(
                "e.g. 'notebook', 'script', or a product-defined type. NOTE: the confirmed live "
                "request body for the simple asset_ref shape has no literal `job_type` field — "
                "this input is forwarded as `asset_ref_type` only when `asset_ref` is omitted "
                "(the rich/product-specific job shape, e.g. Spark applications)."
            )
        ),
    ],
    asset_ref: Annotated[
        Optional[str],
        Field(default=None, description="Id of the project asset the job runs (script/notebook/pipeline). The simple, confirmed-live job shape."),
    ] = None,
    configuration: Annotated[
        Optional[dict],
        Field(
            default=None,
            description=(
                "Fully opaque pass-through object — for the simple asset_ref shape this normalizes "
                "server-side to {'env_type': '', 'env_variables': []}; for the rich asset_ref_type "
                "shape (e.g. Spark) it carries the entire product-specific execution config "
                "(compute, spark_conf, application, ...) unverified/from an example request — this server "
                "does not interpret it either way, only forwards it."
            ),
        ),
    ] = None,
    schedule: Annotated[
        Optional[str],
        Field(default=None, description="Plain cron-expression string, e.g. '0 9 * * *' — confirmed live, NOT an object."),
    ] = None,
    description: Optional[str] = None,
    inject_wxd_credentials: Annotated[
        bool,
        Field(
            description=(
                "Spark jobs only (CPD): add the watsonx.data catalog credential to spark_conf "
                "server-side, from the session's own login. Required for catalog access from a job; "
                "the value never passes through the caller and is never returned."
            )
        ),
    ] = False,
) -> dict:
    """Wraps POST /v2/jobs?project_id=... — confirmed live. Request body is
    {"job": {...}}."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    if inject_wxd_credentials:
        if job_type != "spark_application":
            raise PlatformError("invalid_request", "inject_wxd_credentials only applies to job_type='spark_application'.", False)
        configuration = with_wxd_credentials(state, configuration)

    job_body: dict[str, Any] = {"name": name, "configuration": configuration if configuration is not None else {}}
    if asset_ref:
        job_body["asset_ref"] = asset_ref
    else:
        # Rich/product-specific shape — unverified live on this cluster, from an example request.
        job_body["asset_ref_type"] = job_type
    if description is not None:
        job_body["description"] = description
    if schedule is not None:
        job_body["schedule"] = schedule

    resp = await client.request("POST", "/v2/jobs", params={"project_id": project_id}, json_body={"job": job_body})
    resp = resp or {}
    job_id = resp.get("asset_id") or extract_id(resp)
    return {
        "job_id": job_id,
        "name": name,
        "job_type": job_type,
        "created_at": (resp.get("metadata") or {}).get("created_at"),
    }


@mcp.tool(annotations=ToolAnnotations(title="Update Job", idempotentHint=True))
@redacted
async def update_job(
    ctx: Context,
    project_id: str,
    job_id: str,
    name: Optional[str] = None,
    configuration: Optional[dict] = None,
    schedule: Annotated[Optional[str], Field(default=None, description="Plain cron string.")] = None,
) -> dict:
    """EXPERIMENTAL for schedule edits — Phase 3 (spec/02-tools.md).

    The JSON-Patch (RFC 6902) body format is confirmed live: PATCH
    /v2/jobs/{id} rejects a flat object and requires an array. BUT two
    separate live attempts to change an existing job's `schedule` via this
    PATCH (tried both `/job/schedule` and `/schedule` as the path) returned
    200 without visibly changing the stored schedule value on a follow-up
    GET. Treat schedule edits made through this tool as unverified — name/
    configuration edits use the same confirmed format but weren't the
    specific field that failed to change, so they're more likely to work.
    """
    patch: list[dict[str, Any]] = []
    if name is not None:
        patch.append({"op": "replace", "path": "/job/name", "value": name})
    if configuration is not None:
        patch.append({"op": "replace", "path": "/job/configuration", "value": configuration})
    if schedule is not None:
        # TODO(spec/02-tools.md, UNCONFIRMED): this path did not visibly change the
        # stored schedule in two live attempts. Needs another live pass — possibly a
        # different path, or a dedicated schedule endpoint distinct from job PATCH.
        patch.append({"op": "replace", "path": "/job/schedule", "value": schedule})
    if not patch:
        raise PlatformError("invalid_request", "At least one of name/configuration/schedule must be provided.", False)

    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("PATCH", f"/v2/jobs/{job_id}", params={"project_id": project_id}, json_body=patch)
    if resp is None:
        resp = await client.request("GET", f"/v2/jobs/{job_id}", params={"project_id": project_id})
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="List Jobs", readOnlyHint=True))
@redacted
async def list_jobs(
    ctx: Context,
    project_id: str,
    name_contains: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> dict:
    """Wraps GET /v2/jobs?project_id=.... NOTE: the exact native pagination
    envelope for this endpoint isn't pinned down in the spec (only that the
    read path itself is confirmed reachable) — defensively checks the
    `resources`/`results`/`items` keys, matching whichever shape jobs (as
    assets) actually use."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    limit = clamp_limit(limit)

    params: dict[str, Any] = {"project_id": project_id, "limit": limit}
    if cursor:
        params["bookmark"] = cursor
    resp = await client.request("GET", "/v2/jobs", params=params)
    resources = first_present_list(resp)
    items = [_normalize_job(r) for r in resources]

    if name_contains:
        needle = name_contains.lower()
        items = [it for it in items if it.get("name") and needle in it["name"].lower()]

    bookmark = (resp or {}).get("bookmark")
    next_cursor = bookmark if bookmark and len(resources) >= limit else None
    return {"items": items, "next_cursor": next_cursor}


@mcp.tool(annotations=ToolAnnotations(title="Get Job", readOnlyHint=True))
@redacted
async def get_job(ctx: Context, project_id: str, job_id: str) -> dict:
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", f"/v2/jobs/{job_id}", params={"project_id": project_id})
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Delete Job", destructiveHint=True, idempotentHint=True))
@redacted
async def delete_job(ctx: Context, project_id: str, job_id: str, confirm: bool = False) -> dict:
    """Confirmed live: 204, via the dedicated /v2/jobs/{id} path (the generic
    asset-delete path also works, since jobs are assets, but this uses the
    dedicated form for clarity)."""
    require_confirm(confirm, "delete this job")
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    await client.request("DELETE", f"/v2/jobs/{job_id}", params={"project_id": project_id})
    return {"deleted": True}
