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


# --- get_job_run_logs ---------------------------------------------------------

from watsonx_platform_mcp.tools import job_runs  # noqa: E402

_LOGS_URL = "https://cpd.example.com/v2/jobs/j1/runs/r1/logs"
_LIST_URL = "https://cpd.example.com/v2/asset_files/spark_jobs/j1/r1/logs/"


@pytest.mark.asyncio
@respx.mock
async def test_get_job_run_logs_returns_traceback(cpd_zen_state: SessionState, ctx_factory):
    lines = ["starting", "Traceback (most recent call last):", '  File "x.py", line 2, in <module>', "ValueError: boom"]
    route = respx.get(_LOGS_URL).mock(return_value=Response(200, json={"results": lines}))
    respx.get(_LIST_URL).mock(return_value=Response(404))
    result = await job_runs.get_job_run_logs(ctx_factory(cpd_zen_state), project_id="p1", job_id="j1", run_id="r1")
    assert route.calls[0].request.url.params["project_id"] == "p1"
    assert result == {"lines": lines, "total_lines": 4, "returned_lines": 4, "truncated": False}


@pytest.mark.asyncio
@respx.mock
async def test_get_job_run_logs_tail_head_and_all(cpd_zen_state: SessionState, ctx_factory):
    lines = [f"l{i}" for i in range(10)]
    respx.get(_LOGS_URL).mock(return_value=Response(200, json={"results": lines}))
    respx.get(_LIST_URL).mock(return_value=Response(404))
    ctx = ctx_factory(cpd_zen_state)
    tail = await job_runs.get_job_run_logs(ctx, project_id="p1", job_id="j1", run_id="r1", max_lines=3)
    assert tail["lines"] == ["l7", "l8", "l9"] and tail["truncated"] and tail["total_lines"] == 10
    head = await job_runs.get_job_run_logs(ctx, project_id="p1", job_id="j1", run_id="r1", max_lines=3, tail=False)
    assert head["lines"] == ["l0", "l1", "l2"] and head["truncated"]
    everything = await job_runs.get_job_run_logs(ctx, project_id="p1", job_id="j1", run_id="r1", max_lines=0)
    assert everything["lines"] == lines and not everything["truncated"]


@pytest.mark.asyncio
@respx.mock
async def test_get_job_run_logs_default_cap_and_empty(cpd_zen_state: SessionState, ctx_factory):
    respx.get(_LOGS_URL).mock(return_value=Response(200, json={"results": [f"l{i}" for i in range(500)]}))
    respx.get(_LIST_URL).mock(return_value=Response(404))
    ctx = ctx_factory(cpd_zen_state)
    r = await job_runs.get_job_run_logs(ctx, project_id="p1", job_id="j1", run_id="r1")
    assert r["returned_lines"] == 200 and r["lines"][-1] == "l499" and r["truncated"]
    respx.get(_LOGS_URL).mock(return_value=Response(200, json={}))
    r = await job_runs.get_job_run_logs(ctx, project_id="p1", job_id="j1", run_id="r1")
    assert r == {"lines": [], "total_lines": 0, "returned_lines": 0, "truncated": False}




@pytest.mark.asyncio
@respx.mock
async def test_get_job_run_logs_appends_driver_stdout_with_traceback(cpd_zen_state: SessionState, ctx_factory):
    """Live finding (CPD): /logs held only the platform summary for a failed run; the
    traceback was only in the <id>-stdout file."""
    respx.get(_LOGS_URL).mock(return_value=Response(200, json={"results": ["WARNING: x", "Application is in FAILED state"]}))
    respx.get(_LIST_URL).mock(
        return_value=Response(
            200,
            json={
                "resources": [
                    {"path": "spark_jobs/j1/r1/logs/abc-stdout", "size": 99},
                    {"path": "spark_jobs/j1/r1/logs/abc-spark-submit-return-code", "size": 2},
                    {"path": "spark_jobs/j1/r1/logs/x.worker.Worker-1.out", "size": 9},
                ]
            },
        )
    )
    out = respx.get("https://cpd.example.com/v2/asset_files/spark_jobs/j1/r1/logs/abc-stdout").mock(
        return_value=Response(200, text='WARNING: x\nbefore failure\nTraceback (most recent call last):\n  File "s.py", line 3\nValueError: LIVE_BOOM\n')
    )
    result = await job_runs.get_job_run_logs(ctx_factory(cpd_zen_state), project_id="p1", job_id="j1", run_id="r1")
    assert out.calls[0].request.url.params["project_id"] == "p1"
    assert result["lines"] == [
        "WARNING: x",
        "Application is in FAILED state",
        "=== abc-stdout ===",
        "before failure",
        "Traceback (most recent call last):",
        '  File "s.py", line 3',
        "ValueError: LIVE_BOOM",
    ]
    # Tail keeps the traceback.
    tailed = await job_runs.get_job_run_logs(ctx_factory(cpd_zen_state), project_id="p1", job_id="j1", run_id="r1", max_lines=2)
    assert tailed["lines"][-1] == "ValueError: LIVE_BOOM" and tailed["truncated"]


@pytest.mark.asyncio
@respx.mock
async def test_get_job_run_logs_skips_driver_files_when_listing_fails_or_duplicates(cpd_zen_state: SessionState, ctx_factory):
    respx.get(_LOGS_URL).mock(return_value=Response(200, json={"results": ["hello", "boom"]}))
    respx.get(_LIST_URL).mock(return_value=Response(404, json={"message": "nope"}))
    r = await job_runs.get_job_run_logs(ctx_factory(cpd_zen_state), project_id="p1", job_id="j1", run_id="r1")
    assert r["lines"] == ["hello", "boom"]

    respx.get(_LIST_URL).mock(return_value=Response(200, json={"resources": [{"path": "spark_jobs/j1/r1/logs/a-stdout"}]}))
    respx.get("https://cpd.example.com/v2/asset_files/spark_jobs/j1/r1/logs/a-stdout").mock(return_value=Response(200, text="hello\nboom\n"))
    r = await job_runs.get_job_run_logs(ctx_factory(cpd_zen_state), project_id="p1", job_id="j1", run_id="r1")
    assert r["lines"] == ["hello", "boom"]  # endpoint already had the driver output
