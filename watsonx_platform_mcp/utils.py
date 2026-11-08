"""Small shared helpers used across tool modules."""

from __future__ import annotations

import mimetypes
import os
from typing import Any, Optional


def extract_id(resp: Optional[dict]) -> Optional[str]:
    """Defensive create-response id extraction.

    Confirmed live that create responses are NOT guaranteed to look like read
    responses: create_project returns {"location": "/v2/projects/{id}"} (a
    relative URL to parse), while create_asset/create_job return a top-level
    `asset_id` directly. spec/04-environment-differences.md says to treat this
    defensive extraction as the general pattern for every create_* tool, not
    just create_project: check `location`, then `metadata.guid`, then a
    top-level `guid`/`id`/`asset_id`, in that order.
    """
    if not isinstance(resp, dict):
        return None
    location = resp.get("location")
    if isinstance(location, str) and location:
        return location.rstrip("/").split("/")[-1]
    metadata = resp.get("metadata")
    if isinstance(metadata, dict) and metadata.get("guid"):
        return str(metadata["guid"])
    for key in ("guid", "id", "asset_id"):
        if resp.get(key):
            return str(resp[key])
    return None


def guess_mime_type(name: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


def require_confirm(confirm: bool, action: str) -> None:
    from .errors import PlatformError

    if not confirm:
        raise PlatformError(
            "invalid_request",
            f"`confirm` must be true to {action} — this is a destructive operation.",
            False,
        )


def read_local_file(path: str) -> bytes:
    with open(os.path.expanduser(path), "rb") as fh:
        return fh.read()


WXD_API_KEY_CONF = "spark.hadoop.wxd.apiKey"


def wxd_spark_api_key(state) -> str:
    """The watsonx.data Spark credential: "ZenApiKey " + base64(username:apikey).

    Built from the session's own credentials so it never passes through the caller.
    CPD only (needs a username + API key); never log or return the result."""
    import base64

    from .errors import PlatformError

    if state.environment != "cpd" or not state.username or not state.apikey:
        raise PlatformError(
            "invalid_request",
            "inject_wxd_credentials needs a CPD session authenticated with a username and API key "
            "(not available for SaaS or username/password sessions).",
            False,
        )
    return "ZenApiKey " + base64.b64encode(f"{state.username}:{state.apikey}".encode()).decode()


def with_wxd_credentials(state, configuration: Optional[dict]) -> dict:
    """Copy of `configuration` with the credential added to spark_conf."""
    cfg = dict(configuration or {})
    conf = dict(cfg.get("spark_conf") or {})
    conf[WXD_API_KEY_CONF] = wxd_spark_api_key(state)
    cfg["spark_conf"] = conf
    return cfg
