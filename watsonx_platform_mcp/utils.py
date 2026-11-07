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
