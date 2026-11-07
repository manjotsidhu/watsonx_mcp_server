"""Phase 3 tools: explicitly low-confidence or entirely untested per the spec.

Below the line for each tool: unit tests against our own mocked-HTTP plumbing
(request shapes we control, confirm-guard enforcement) — these verify our
code does what we intended. Skipped integration stubs mark behavior this
server cannot actually know without a live cluster; they are placeholders
for a live re-verification pass, not assertions of correctness.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.session import SessionState
from watsonx_platform_mcp.tools import job_runs, members, assets


# --- job_runs: structural plumbing, from an unverified example request ---------


@pytest.mark.asyncio
@respx.mock
async def test_create_job_run_wraps_body_in_job_run_key(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed in an example request (not independently re-verified live):
    {"job_run": {...}}, NOT {"run": {...}}."""
    route = respx.post("https://cpd.example.com/v2/jobs/job-1/runs").mock(
        return_value=Response(201, json={"run_id": "run-1", "state": "Starting"})
    )
    ctx = ctx_factory(cpd_zen_state)
    result = await job_runs.create_job_run(ctx, project_id="p1", job_id="job-1")
    body = json.loads(route.calls[0].request.content)
    assert "job_run" in body
    assert "run" not in body
    assert result == {"run_id": "run-1", "state": "Starting"}


@pytest.mark.asyncio
@respx.mock
async def test_cancel_job_run_is_post_not_delete(cpd_zen_state: SessionState, ctx_factory):
    """Confirmed in an example request: POST .../cancel, not DELETE."""
    route = respx.post("https://cpd.example.com/v2/jobs/job-1/runs/run-1/cancel").mock(
        return_value=Response(200, json={"state": "Cancelled"})
    )
    ctx = ctx_factory(cpd_zen_state)
    result = await job_runs.cancel_job_run(ctx, project_id="p1", job_id="job-1", run_id="run-1", confirm=True)
    assert route.called
    assert result == {"cancelled": True, "state": "Cancelled"}


@pytest.mark.asyncio
async def test_cancel_job_run_requires_confirm(cpd_zen_state: SessionState, ctx_factory):
    """confirm isn't in 02-tools.md's literal per-tool contract for this one,
    but 03-conventions.md's blanket destructive-op rule applies — added for
    consistency."""
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError):
        await job_runs.cancel_job_run(ctx, project_id="p1", job_id="job-1", run_id="run-1", confirm=False)


@pytest.mark.skip(reason="Never run against a live cluster during spec validation — from a third-party example request only. Re-verify list_job_runs/get_job_run response shapes live before trusting normalization.")
def test_job_run_lifecycle_live_shapes_pending_verification():
    pass


# --- members: zero testing done during spec validation ---------------------


@pytest.mark.asyncio
@respx.mock
async def test_add_project_member_request_shape(cpd_zen_state: SessionState, ctx_factory):
    route = respx.post("https://cpd.example.com/v2/projects/p1/members").mock(return_value=Response(200, json={}))
    ctx = ctx_factory(cpd_zen_state)
    result = await members.add_project_member(ctx, project_id="p1", identity="user@example.com", role="editor")
    body = json.loads(route.calls[0].request.content)
    assert body == {"members": [{"user_iam_id": "user@example.com", "role": "editor"}]}
    assert result == {"added": True, "identity": "user@example.com", "role": "editor"}


@pytest.mark.asyncio
async def test_remove_project_member_requires_confirm(cpd_zen_state: SessionState, ctx_factory):
    ctx = ctx_factory(cpd_zen_state)
    with pytest.raises(PlatformError):
        await members.remove_project_member(ctx, project_id="p1", identity="user@example.com", confirm=False)


@pytest.mark.skip(reason="Zero testing was done on the entire project-member capability during spec validation (spec/02-tools.md #9). Every field name and endpoint here is an unverified assumption, including whether `identity` doubles as `{member_id}` in the path — needs a full live pass.")
def test_project_members_live_endpoint_shapes_pending_verification():
    pass


# --- upload_asset_file: only the create-data-asset step is verified --------


@pytest.mark.asyncio
@respx.mock
async def test_upload_asset_file_create_step_uses_data_asset_entity_shape(cpd_zen_state: SessionState, ctx_factory, tmp_path):
    """Only this first step (create_asset with the data_asset entity shape) is
    confirmed-ish (from an example request) — the rest of this test's mocked chain
    is this server's own best-effort, unconfirmed sequence."""
    f = tmp_path / "script.py"
    f.write_text("print('hi')")

    create_route = respx.post("https://cpd.example.com/v2/assets").mock(return_value=Response(201, json={"asset_id": "a1"}))
    attach_route = respx.post("https://cpd.example.com/v2/assets/a1/attachments").mock(
        return_value=Response(201, json={"attachment_id": "att-1"})
    )
    respx.put(
        "https://cpd.example.com/v2/assets/a1/attachments/att-1"
    ).mock(return_value=Response(200, json={}))
    respx.post("https://cpd.example.com/v2/assets/a1/attachments/att-1/complete").mock(return_value=Response(404))

    ctx = ctx_factory(cpd_zen_state)
    result = await assets.upload_asset_file(ctx, project_id="p1", name="script.py", file_path=str(f))

    create_body = json.loads(create_route.calls[0].request.content)
    assert create_body["entity"]["data_asset"]["mime_type"] == "text/x-python"
    assert json.loads(attach_route.calls[0].request.content)["object_key"] == "/data_asset/script.py"
    assert result == {"asset_id": "a1", "attachment_id": "att-1", "name": "script.py"}


@pytest.mark.skip(reason="spec/02-tools.md: the attachment->signed-URL->complete sequence beyond asset creation is UNCONFIRMED — no live cluster pass has exercised the real signed-URL PUT step or confirmed a 'complete' call is even required. This server's fallback behavior (PUT to the attachment endpoint, best-effort 'complete' call ignoring 404) is a guess pending live re-verification.")
def test_upload_asset_file_full_live_sequence_pending_verification():
    pass
