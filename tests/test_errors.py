"""Error normalization against the exact envelope shapes captured in spec/03-conventions.md."""

from __future__ import annotations

import httpx
import pytest

from watsonx_platform_mcp.errors import normalize_http_error


def _resp(status: int, json_body: dict | None = None, text: str = "", headers: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://example.com/x")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=request, headers=headers or {})
    return httpx.Response(status, text=text, request=request, headers=headers or {})


def test_400_wscpa_shape():
    body = {
        "id": "WSCPA0000E",
        "code": 400,
        "error": "Bad Request",
        "reason": "Failed to parse request parameters: type query parameter value is invalid: bogus-garbage-value.",
        "message": "The server cannot or will not process the request due to an apparent client error.",
    }
    err = normalize_http_error(_resp(400, body))
    assert err.code == "invalid_request"
    assert err.retryable is False
    assert "bogus-garbage-value" in err.message


def test_401_authorize_shape():
    body = {"_statusCode_": 401, "exception": "Sorry, we could not verify your credentials.", "message": "Unauthorized"}
    err = normalize_http_error(_resp(401, body))
    assert err.code == "unauthorized"
    assert err.retryable is False
    assert err.message == "Sorry, we could not verify your credentials."


def test_400_transactional_missing_storage_shape():
    body = {
        "code": 400,
        "error": "Bad Request",
        "reason": "Missing required properties for project: storage",
        "message": "...",
        "description": "[400] Bad Request: ...",
        "transactionId": "abc-123",
    }
    err = normalize_http_error(_resp(400, body))
    assert err.code == "invalid_request"
    assert err.message == "Missing required properties for project: storage"


def test_connections_errors_array_shape():
    body = {
        "trace": "xyz",
        "errors": [
            {
                "code": "invalid_payload",
                "message": "CDICO2034E: One of the properties [host, password, port, server, username] is required.",
                "more_info": "null",
                "extra": {"environment_name": "icp4data", "http_status": 400, "id": "CDICO9007E"},
            }
        ],
    }
    err = normalize_http_error(_resp(400, body))
    assert err.code == "invalid_request"
    assert "CDICO2034E" in err.message


def test_460_connection_test_failed():
    body = {
        "errors": [
            {
                "code": "invalid_data",
                "message": "The connection could not be established: CDICO0103E: [IBM Cloud Object Storage] Invalid username or password.",
                "more_info": "Check the connection properties and ensure that the resource is accessible.",
            }
        ]
    }
    err = normalize_http_error(_resp(460, body))
    assert err.code == "connection_test_failed"
    assert err.retryable is False


@pytest.mark.parametrize(
    "status,expected_code,expected_retryable",
    [
        (403, "forbidden", False),
        (404, "not_found", False),
        (409, "conflict", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_status_code_mapping(status, expected_code, expected_retryable):
    err = normalize_http_error(_resp(status, {"message": "boom"}))
    assert err.code == expected_code
    assert err.retryable is expected_retryable


def test_429_honors_retry_after():
    err = normalize_http_error(_resp(429, {"message": "slow down"}, headers={"Retry-After": "30"}))
    assert err.retry_after == "30"


def test_non_json_body_falls_back_to_text():
    err = normalize_http_error(_resp(500, text="upstream is on fire"))
    assert "upstream is on fire" in err.message
