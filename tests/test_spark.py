"""Credential injection, delete_asset and run_spark_script."""

from __future__ import annotations

import base64
import json

import pytest
import respx
from httpx import Response

from watsonx_platform_mcp.errors import PlatformError
from watsonx_platform_mcp.tools import assets, jobs, spark

B = "https://cpd.example.com"
KEY = "ZenApiKey " + base64.b64encode(b"user:key").decode()  # fixture creds: user / key
ENC_URL = f"{B}/wxd/platform/api/v1/spark_application/jobs/_encrypt"


def _mock_encrypt():
    """Echo the job back with the credential replaced by ciphertext, like the real endpoint."""

    def handler(request):
        job = json.loads(request.content)["job"]
        job["configuration"]["spark_conf"]["spark.hadoop.wxd.apiKey"] = "ENCRYPTED-BLOB"
        return Response(200, json={"job": job})

    return respx.post(ENC_URL).mock(side_effect=handler)


COMPUTE = dict(instance_id="i1", engine_id="spark503", engine_name="spark-5")


@pytest.mark.asyncio
@respx.mock
async def test_create_job_injects_credential_server_side_and_never_returns_it(cpd_zen_state, ctx_factory):
    enc = _mock_encrypt()
    route = respx.post(f"{B}/v2/jobs").mock(return_value=Response(201, json={"asset_id": "j1"}))
    cfg = {"spark_conf": {"spark.eventLog.enabled": "true"}}
    result = await jobs.create_job(
        ctx_factory(cpd_zen_state), project_id="p", name="n", job_type="spark_application",
        configuration=cfg, inject_wxd_credentials=True,
    )
    # The plaintext goes only to the encrypt endpoint; /v2/jobs gets the ciphertext.
    assert json.loads(enc.calls[0].request.content)["job"]["configuration"]["spark_conf"]["spark.hadoop.wxd.apiKey"] == KEY
    assert enc.calls[0].request.url.params["project_id"] == "p"
    assert KEY not in route.calls[0].request.content.decode()
    sent = json.loads(route.calls[0].request.content)["job"]["configuration"]["spark_conf"]
    assert sent["spark.hadoop.wxd.apiKey"] == "ENCRYPTED-BLOB" and sent["spark.eventLog.enabled"] == "true"
    assert "spark.hadoop.wxd.apiKey" not in cfg["spark_conf"]  # caller's dict untouched
    assert KEY not in json.dumps(result)


@pytest.mark.asyncio
async def test_inject_rejected_for_saas_and_non_spark(saas_iam_state, cpd_zen_state, ctx_factory):
    with pytest.raises(PlatformError) as e:
        await jobs.create_job(ctx_factory(saas_iam_state), project_id="p", name="n", job_type="spark_application", inject_wxd_credentials=True)
    assert e.value.code == "invalid_request"
    with pytest.raises(PlatformError):
        await jobs.create_job(ctx_factory(cpd_zen_state), project_id="p", name="n", job_type="notebook", asset_ref="a", inject_wxd_credentials=True)


@pytest.mark.asyncio
@respx.mock
async def test_create_job_fails_closed_when_encryption_unavailable(cpd_zen_state, ctx_factory):
    respx.post(ENC_URL).mock(return_value=Response(404, json={"message": "nope"}))
    create = respx.post(f"{B}/v2/jobs").mock(return_value=Response(201, json={"asset_id": "j1"}))
    with pytest.raises(PlatformError) as e:
        await jobs.create_job(ctx_factory(cpd_zen_state), project_id="p", name="n", job_type="spark_application", inject_wxd_credentials=True)
    assert not create.called
    assert KEY not in str(e.value) and e.value.http_status == 404


@pytest.mark.asyncio
@respx.mock
async def test_create_job_rejects_malformed_encrypt_response(cpd_zen_state, ctx_factory):
    respx.post(ENC_URL).mock(return_value=Response(200, json={"unexpected": 1}))
    create = respx.post(f"{B}/v2/jobs").mock(return_value=Response(201, json={"asset_id": "j1"}))
    with pytest.raises(PlatformError):
        await jobs.create_job(ctx_factory(cpd_zen_state), project_id="p", name="n", job_type="spark_application", inject_wxd_credentials=True)
    assert not create.called


@pytest.mark.asyncio
@respx.mock
async def test_delete_asset_requires_confirm(cpd_zen_state, ctx_factory):
    route = respx.delete(f"{B}/v2/assets/a1").mock(return_value=Response(204))
    with pytest.raises(PlatformError):
        await assets.delete_asset(ctx_factory(cpd_zen_state), project_id="p", asset_id="a1")
    assert not route.called
    assert await assets.delete_asset(ctx_factory(cpd_zen_state), project_id="p", asset_id="a1", confirm=True) == {"deleted": True}


def _mock_upload():
    respx.put(url__regex=rf"{B}/v2/asset_files/data_asset/.+").mock(return_value=Response(201, json={}))
    respx.post(f"{B}/v2/assets").mock(return_value=Response(201, json={"asset_id": "a1"}))
    respx.post(f"{B}/v2/assets/a1/attachments").mock(return_value=Response(201, json={"attachment_id": "att"}))


@pytest.mark.asyncio
@respx.mock
async def test_run_spark_script_happy_path(cpd_zen_state, ctx_factory):
    _mock_upload()
    _mock_encrypt()
    job = respx.post(f"{B}/v2/jobs").mock(return_value=Response(201, json={"asset_id": "j1"}))
    run = respx.post(f"{B}/v2/jobs/j1/runs").mock(
        return_value=Response(201, json={"metadata": {"asset_id": "r1"}, "entity": {"job_run": {"state": "Starting"}}})
    )
    out = await spark.run_spark_script(ctx_factory(cpd_zen_state), project_id="p", script="print(1)", **COMPUTE)
    assert (out["asset_id"], out["job_id"], out["run_id"], out["state"]) == ("a1", "j1", "r1", "Starting")
    assert KEY not in json.dumps(out)
    body = json.loads(job.calls[0].request.content)["job"]
    cfg = body["configuration"]
    assert body["asset_ref_type"] == "spark_application"
    assert cfg["application"] == {"type": "data_asset", "asset_id": "a1"}
    assert cfg["compute"] == {"instance_id": "i1", "engine_id": "spark503", "engine_name": "spark-5"}
    assert cfg["upload_id"] and cfg["spark_conf"]["spark.hadoop.wxd.apiKey"] == "ENCRYPTED-BLOB"
    assert body["name"] == out["script_name"].removesuffix(".py")  # unique, derived from the same suffix
    assert run.called


@pytest.mark.asyncio
@respx.mock
async def test_run_spark_script_rolls_back_job_and_asset_on_failure(cpd_zen_state, ctx_factory):
    _mock_upload()
    _mock_encrypt()
    respx.post(f"{B}/v2/jobs").mock(return_value=Response(201, json={"asset_id": "j1"}))
    respx.post(f"{B}/v2/jobs/j1/runs").mock(return_value=Response(500, json={"message": "boom"}))
    del_job = respx.delete(f"{B}/v2/jobs/j1").mock(return_value=Response(204))
    del_asset = respx.delete(f"{B}/v2/assets/a1").mock(return_value=Response(204))
    with pytest.raises(PlatformError):
        await spark.run_spark_script(ctx_factory(cpd_zen_state), project_id="p", script="x", **COMPUTE)
    assert del_job.called and del_asset.called
