"""One-call Spark script runner — see spec/02-tools.md "run_spark_script"."""

from __future__ import annotations

import base64
import uuid
from typing import Annotated, Optional

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from ..app import mcp
from ..bootstrap import ensure_authenticated
from ..errors import PlatformError
from ..http import PlatformClient
from . import assets, job_runs, jobs, redacted


@mcp.tool(annotations=ToolAnnotations(title="Run Spark Script", destructiveHint=False))
@redacted
async def run_spark_script(
    ctx: Context,
    project_id: str,
    script: Annotated[str, Field(description="PySpark script source text.")],
    instance_id: Annotated[str, Field(description="Spark engine's instance id (compute.instance_id).")],
    engine_id: Annotated[str, Field(description="e.g. 'spark503'.")],
    engine_name: Annotated[str, Field(description="e.g. 'spark-5'.")],
    name_prefix: Annotated[str, Field(description="Prefix for the generated, unique asset/job names.")] = "spark-script",
    spark_version: str = "4.0",
    arguments: Optional[list[str]] = None,
    spark_conf: Optional[dict] = None,
    inject_wxd_credentials: Annotated[
        bool, Field(description="Add the watsonx.data catalog credential to spark_conf server-side (CPD).")
    ] = True,
) -> dict:
    """Upload `script` as a data asset, create a Spark job for it and start a run.
    Returns immediately: poll get_job_run, then read get_job_run_logs. If a step
    fails, the job/asset created so far are deleted. On success the asset and job
    are kept (the logs live under the job) — remove with delete_job / delete_asset."""
    suffix = uuid.uuid4().hex[:8]
    fname = f"{name_prefix}-{suffix}.py"
    state = await ensure_authenticated(ctx)
    client = PlatformClient(state)

    up = await assets.upload_asset_file(
        ctx, project_id=project_id, name=fname,
        content_base64=base64.b64encode(script.encode()).decode(), mime_type="text/x-python",
    )
    job_id: Optional[str] = None
    try:
        conf = {"spark.eventLog.enabled": "true", **(spark_conf or {})}
        cfg = {
            "upload_id": str(uuid.uuid4()),
            "application": {"type": "data_asset", "asset_id": up["asset_id"]},
            "compute": {"instance_id": instance_id, "engine_id": engine_id, "engine_name": engine_name},
            "spark_version": spark_version,
            "spark_conf": conf,
            "spark_env": {},
            "arguments": arguments or [],
        }
        job = await jobs.create_job(
            ctx, project_id=project_id, name=f"{name_prefix}-{suffix}", job_type="spark_application",
            configuration=cfg, inject_wxd_credentials=inject_wxd_credentials,
        )
        job_id = job["job_id"]
        if not job_id:
            raise PlatformError("upstream_error", "Job creation returned no job_id.", False)
        run = await job_runs.create_job_run(ctx, project_id=project_id, job_id=job_id, configuration={})
    except BaseException:
        for path in ([f"/v2/jobs/{job_id}"] if job_id else []) + [f"/v2/assets/{up['asset_id']}"]:
            try:
                await client.request("DELETE", path, params={"project_id": project_id})
            except PlatformError:
                pass
        raise
    return {"asset_id": up["asset_id"], "job_id": job_id, "run_id": run["run_id"], "state": run["state"], "script_name": fname}
