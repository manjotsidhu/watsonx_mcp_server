"""Jobs tools — spec/02-tools.md #10-12."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.session import SessionState
from watsonx_platform_mcp.tools import jobs


@pytest.mark.asyncio
@respx.mock
async def test_create_job_simple_asset_ref_shape_confirmed_envelope(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: request {"job": {"name":..., "asset_ref":..., "configuration": {}}}."""
    response_body = {"asset_id": "job-1", "metadata": {"created_at": "2024-01-01T00:00:00Z"}, "entity": {"job": {}}}
    route = respx.post("https://cpd.example.com/v2/jobs").mock(return_value=Response(201, json=response_body))
    ctx = ctx_factory(cpd_zen_state)

    result = await jobs.create_job(ctx, project_id="p1", name="my-job", job_type="notebook", asset_ref="asset-1")

    assert result == {"job_id": "job-1", "name": "my-job", "job_type": "notebook", "created_at": "2024-01-01T00:00:00Z"}
    body = json.loads(route.calls[0].request.content)
    assert body == {"job": {"name": "my-job", "configuration": {}, "asset_ref": "asset-1"}}
    # job_type is NOT forwarded as a literal field when asset_ref is used —
    # matches the confirmed live payload, which has no `job_type` key at all.
    assert "job_type" not in body["job"]


@pytest.mark.asyncio
@respx.mock
async def test_create_job_schedule_is_a_plain_cron_string(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: schedule is a plain cron string, NOT an object — an
    object is rejected with 400 '.../schedule must be string'."""
    route = respx.post("https://cpd.example.com/v2/jobs").mock(return_value=Response(201, json={"asset_id": "job-2"}))
    ctx = ctx_factory(cpd_zen_state)
    await jobs.create_job(ctx, project_id="p1", name="scheduled-job", job_type="notebook", asset_ref="asset-1", schedule="0 9 * * *")
    body = json.loads(route.calls[0].request.content)
    assert body["job"]["schedule"] == "0 9 * * *"
    assert isinstance(body["job"]["schedule"], str)


@pytest.mark.asyncio
@respx.mock
async def test_create_job_rich_shape_uses_asset_ref_type_when_no_asset_ref(cpd_zen_state: SessionState, ctx_factory):
    """Rich/product-specific shape from an example request — asset_ref_type alone,
    opaque configuration pass-through."""
    route = respx.post("https://cpd.example.com/v2/jobs").mock(return_value=Response(201, json={"asset_id": "job-3"}))
    ctx = ctx_factory(cpd_zen_state)
    spark_config = {
        "upload_id": "u1",
        "application": {"type": "data_asset", "asset_id": "a1"},
        "compute": {"instance_name": "lakehouse", "engine_id": "spark335"},
        "spark_version": "3.4",
    }
    await jobs.create_job(ctx, project_id="p1", name="spark-job", job_type="spark_application", configuration=spark_config)
    body = json.loads(route.calls[0].request.content)
    assert body["job"]["asset_ref_type"] == "spark_application"
    assert "asset_ref" not in body["job"]
    assert body["job"]["configuration"] == spark_config  # fully opaque pass-through, untouched


@pytest.mark.asyncio
@respx.mock
async def test_update_job_requires_json_patch_array_not_flat_object(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed live: PATCH /v2/jobs/{id} needs an RFC 6902 array — contrast
    update_project's flat object."""
    route = respx.patch("https://cpd.example.com/v2/jobs/job-1").mock(return_value=Response(200, json={"entity": {}}))
    ctx = ctx_factory(cpd_zen_state)
    await jobs.update_job(ctx, project_id="p1", job_id="job-1", schedule="0 20 * * *")
    body = json.loads(route.calls[0].request.content)
    assert isinstance(body, list)
    assert body == [{"op": "replace", "path": "/job/schedule", "value": "0 20 * * *"}]


@pytest.mark.asyncio
async def test_update_job_requires_at_least_one_field(cpd_zen_state: SessionState, ctx_factory):
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError):
        await jobs.update_job(ctx, project_id="p1", job_id="job-1")


@pytest.mark.asyncio
@respx.mock
async def test_delete_job_uses_dedicated_path_and_requires_confirm(cpd_zen_state: SessionState, ctx_factory):
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError):
        await jobs.delete_job(ctx, project_id="p1", job_id="job-1", confirm=False)

    respx.delete("https://cpd.example.com/v2/jobs/job-1").mock(return_value=Response(204))
    result = await jobs.delete_job(ctx, project_id="p1", job_id="job-1", confirm=True)
    assert result == {"deleted": True}


@pytest.mark.asyncio
@respx.mock
async def test_list_jobs_defensive_envelope_lookup(cpd_zen_state: SessionState, ctx_factory):
    """Native envelope for jobs isn't pinned down in the spec; defensively checks
    resources/results/items."""
    body = {"results": [{"metadata": {"asset_id": "job-1", "name": "j1", "asset_type": "job"}, "entity": {"job": {"schedule": "0 9 * * *"}}}]}
    respx.get("https://cpd.example.com/v2/jobs").mock(return_value=Response(200, json=body))
    ctx = ctx_factory(cpd_zen_state)
    result = await jobs.list_jobs(ctx, project_id="p1")
    assert result["items"] == [{"job_id": "job-1", "name": "j1", "job_type": "job", "schedule_enabled": True}]
