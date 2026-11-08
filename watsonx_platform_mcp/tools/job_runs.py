"""Job runs — see spec/02-tools.md #12-13.

EXPERIMENTAL — Phase 3. All four tools here come from an unverified
third-party example request, not independently re-verified live in this spec's
own validation passes. Implemented from the documented contract; treat every
field name and envelope shape as provisional until re-checked live.
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
from ..utils import extract_id, require_confirm
from . import redacted


def _normalize_run(resource: dict) -> dict:
    metadata = resource.get("metadata", {}) or resource
    entity = resource.get("entity", {}) or {}
    run = entity.get("job_run", entity) or {}
    return {
        "run_id": metadata.get("asset_id") or metadata.get("id") or resource.get("run_id"),
        "state": run.get("state") or resource.get("state"),
        "created_at": metadata.get("created_at"),
        "duration": run.get("duration"),
    }


@mcp.tool(annotations=ToolAnnotations(title="List Job Runs", readOnlyHint=True))
@redacted
async def list_job_runs(
    ctx: Context,
    project_id: str,
    job_id: str,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> dict:
    """EXPERIMENTAL — from an unverified example request. Wraps GET /v2/jobs/{id}/runs?project_id=..."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    limit = clamp_limit(limit)

    params: dict[str, Any] = {"project_id": project_id, "limit": limit}
    if cursor:
        params["bookmark"] = cursor
    resp = await client.request("GET", f"/v2/jobs/{job_id}/runs", params=params)
    resources = first_present_list(resp, keys=("runs", "resources", "results", "items"))
    items = [_normalize_run(r) for r in resources]

    bookmark = (resp or {}).get("bookmark")
    next_cursor = bookmark if bookmark and len(resources) >= limit else None
    return {"items": items, "next_cursor": next_cursor}


@mcp.tool(annotations=ToolAnnotations(title="Get Job Run", readOnlyHint=True))
@redacted
async def get_job_run(ctx: Context, project_id: str, job_id: str, run_id: str) -> dict:
    """EXPERIMENTAL — from an unverified example request. Wraps
    GET /v2/jobs/{id}/runs/{run_id}?project_id=.... Run logs
    are fetched by get_job_run_logs."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", f"/v2/jobs/{job_id}/runs/{run_id}", params={"project_id": project_id})
    return resp or {}


@mcp.tool(annotations=ToolAnnotations(title="Cancel Job Run", destructiveHint=True, idempotentHint=True))
@redacted
async def cancel_job_run(ctx: Context, project_id: str, job_id: str, run_id: str, confirm: bool = False) -> dict:
    """EXPERIMENTAL — from an unverified example request. Confirmed to be POST (not
    DELETE) in that example: POST /v2/jobs/{id}/runs/{run_id}/cancel?project_id=....
    A separate DELETE .../runs/{run_id} endpoint also exists and is distinct from
    cancel (presumably deletes the run record instead of stopping it) — not
    exposed as its own tool in v1.

    NOTE: `confirm` isn't listed in 02-tools.md's per-tool contract for this
    one, but 03-conventions.md's blanket destructive-operation rule ("any
    cancel_* tool requires confirm: true") applies here too — added for
    consistency with that stated hard requirement."""
    require_confirm(confirm, "cancel this job run")
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("POST", f"/v2/jobs/{job_id}/runs/{run_id}/cancel", params={"project_id": project_id})
    resp = resp or {}
    return {"cancelled": True, "state": resp.get("state")}


@mcp.tool(annotations=ToolAnnotations(title="Create Job Run", destructiveHint=False))
@redacted
async def create_job_run(
    ctx: Context,
    project_id: str,
    job_id: str,
    configuration: Optional[dict] = None,
) -> dict:
    """EXPERIMENTAL — from an unverified example request. Wraps
    POST /v2/jobs/{id}/runs?project_id=..., body {"job_run": {...}} (confirmed
    wrapper key in that example — NOT {"run": {...}}). Returns immediately with
    the run's current state; does not block until completion (see
    spec/03-conventions.md "Async operations"). Poll get_job_run/list_job_runs."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    job_run: dict[str, Any] = {}
    if configuration is not None:
        job_run["configuration"] = configuration

    resp = await client.request("POST", f"/v2/jobs/{job_id}/runs", params={"project_id": project_id}, json_body={"job_run": job_run})
    resp = resp or {}
    run_id = resp.get("run_id") or extract_id(resp) or (resp.get("metadata") or {}).get("asset_id")
    entity = resp.get("entity", {}) or {}
    run = entity.get("job_run", entity) or {}
    return {"run_id": run_id, "state": run.get("state") or resp.get("state")}


_DRIVER_LOG_SUFFIXES = ("-stdout", "-stderr")


async def _driver_log_files(client: PlatformClient, project_id: str, job_id: str, run_id: str) -> list[tuple[str, list[str]]]:
    """Best-effort: driver stdout/stderr files under /v2/asset_files/spark_jobs/{job}/{run}/logs/.

    Live-verified on CPD: for a failed run, GET .../runs/{run}/logs returned only the
    6-line platform summary, while the script's prints and Python traceback were only in
    the `<id>-stdout` file of this listing. Returns [] on any failure (e.g. non-Spark job)."""
    try:
        listing = await client.request(
            "GET", f"/v2/asset_files/spark_jobs/{job_id}/{run_id}/logs/", params={"project_id": project_id}
        )
        files = [r.get("path", "") for r in ((listing or {}).get("resources") or []) if isinstance(r, dict)]
        out: list[tuple[str, list[str]]] = []
        for path in sorted(f for f in files if f.endswith(_DRIVER_LOG_SUFFIXES)):
            text = await client.get_text(f"/v2/asset_files/{path}", params={"project_id": project_id})
            out.append((path.rsplit("/", 1)[-1], text.splitlines()))
        return out
    except PlatformError:
        return []


@mcp.tool(annotations=ToolAnnotations(title="Get Job Run Logs", readOnlyHint=True))
@redacted
async def get_job_run_logs(
    ctx: Context,
    project_id: str,
    job_id: str,
    run_id: str,
    max_lines: Annotated[
        int, Field(ge=0, description="Maximum log lines to return. 0 = all lines (can be very large).")
    ] = 200,
    tail: Annotated[bool, Field(description="If true keep the LAST max_lines lines (usual choice: tracebacks are at the end); else the first.")] = True,
) -> dict:
    """Logs of a job run: platform status lines from
    GET /v2/jobs/{id}/runs/{run_id}/logs?project_id=... ({"results": [lines]}) followed by
    the Spark driver stdout/stderr files (script prints, Python tracebacks) from
    /v2/asset_files/spark_jobs/{id}/{run_id}/logs/, which on CPD can hold output the first
    endpoint lacks (live-verified). Driver lines already present in the first endpoint's
    output are not repeated. Output is trimmed to `max_lines` (default last 200) so it does
    not flood the caller's context; `total_lines`/`truncated` say what was cut."""
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)
    resp = await client.request("GET", f"/v2/jobs/{job_id}/runs/{run_id}/logs", params={"project_id": project_id})
    raw = (resp or {}).get("results") if isinstance(resp, dict) else None
    lines = [l if isinstance(l, str) else str(l) for l in (raw or [])]

    seen = {l for l in lines if l.strip()}
    for fname, flines in await _driver_log_files(client, project_id, job_id, run_id):
        new = [l for l in flines if not l.strip() or l not in seen]
        if any(l.strip() for l in new):
            lines.append(f"=== {fname} ===")
            lines.extend(new)

    total = len(lines)
    if max_lines and total > max_lines:
        lines = lines[-max_lines:] if tail else lines[:max_lines]
    return {"lines": lines, "total_lines": total, "returned_lines": len(lines), "truncated": len(lines) < total}
