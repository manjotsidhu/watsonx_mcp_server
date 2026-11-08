"""Generic credential redaction — spec/03-conventions.md "Security / redaction"."""

from __future__ import annotations

from watsonx_platform_mcp.redaction import redact


def test_redacts_project_storage_credentials_saas_finding():
    """Reconstructs the shape of the confirmed live SaaS finding (credentials
    embedded in entity.storage.properties.credentials), using placeholder
    values only — the real leaked response was deleted immediately during
    spec validation, not retained anywhere."""
    project_doc = {
        "metadata": {"guid": "proj-1"},
        "entity": {
            "name": "my-project",
            "storage": {
                "type": "bmcos_object_storage",
                "guid": "cos-guid",
                "properties": {
                    "bucket_name": "my-bucket",
                    "credentials": {
                        "admin": {"api_key": "FAKE-ADMIN-KEY", "access_key_id": "FAKE-AKID", "secret_access_key": "FAKE-SECRET"},
                        "editor": {"api_key": "FAKE-EDITOR-KEY"},
                    },
                },
            },
        },
    }
    result = redact(project_doc)
    assert result["entity"]["storage"]["properties"]["credentials"] == "***REDACTED***"
    # Non-secret fields survive untouched.
    assert result["entity"]["name"] == "my-project"
    assert result["entity"]["storage"]["properties"]["bucket_name"] == "my-bucket"
    assert result["metadata"]["guid"] == "proj-1"


def test_redacts_nested_secret_keys_recursively_anywhere():
    doc = {"a": {"b": [{"password": "hunter2"}, {"fine": "ok"}]}, "access_key_id": "AKID"}
    result = redact(doc)
    assert result["a"]["b"][0]["password"] == "***REDACTED***"
    assert result["a"]["b"][1]["fine"] == "ok"
    assert result["access_key_id"] == "***REDACTED***"


def test_redact_is_case_insensitive_on_key_names():
    doc = {"API_KEY": "secret", "ApiKey": "secret2"}
    result = redact(doc)
    assert result["API_KEY"] == "***REDACTED***"
    assert result["ApiKey"] == "***REDACTED***"


def test_redact_passes_through_scalars_and_lists():
    assert redact(5) == 5
    assert redact("hello") == "hello"
    assert redact(None) is None
    assert redact([1, 2, {"password": "x"}]) == [1, 2, {"password": "***REDACTED***"}]


def test_redacts_wxd_spark_credential_by_key_and_by_value():
    doc = {"configuration": {"spark_conf": {"spark.hadoop.wxd.apiKey": "ZenApiKey abc", "spark.eventLog.enabled": "true"}}, "x": ["ZenApiKey zzz"]}
    out = redact(doc)
    assert out["configuration"]["spark_conf"]["spark.hadoop.wxd.apiKey"] == "***REDACTED***"
    assert out["configuration"]["spark_conf"]["spark.eventLog.enabled"] == "true"
    assert out["x"] == ["***REDACTED***"]
