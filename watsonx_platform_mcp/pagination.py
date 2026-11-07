"""Pagination normalization — see spec/03-conventions.md "Pagination".

Confirmed live: three different native pagination shapes exist on the same
platform (bookmark, total_rows/results with a `next` object, and HAL-style
`href` links). Every `list_*` tool exposes the same uniform `limit`/`cursor`
in, `items`/`next_cursor` out contract; this module holds the per-shape
codec so the opaque `cursor` string can round-trip through whatever the
native shape actually needs.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Optional
from urllib.parse import urlsplit

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def encode_json_cursor(obj: Any) -> str:
    """Opaque cursor wrapping an arbitrary JSON-able object (e.g. asset search's `next`)."""
    return base64.urlsafe_b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def decode_json_cursor(cursor: str) -> Any:
    return json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))


def bookmark_next_cursor(resources: list, bookmark: Optional[str], limit: int) -> Optional[str]:
    """Projects-style: {total_results, bookmark, resources: []}.

    The API doesn't document an explicit "no more pages" signal beyond a
    short page — heuristic: fewer results than requested means last page.
    TODO(spec/00-overview.md, unconfirmed): re-verify this heuristic against a
    real multi-page project listing; the spec only confirms this for asset
    search's empty-result case, not projects specifically.
    """
    if not bookmark or len(resources) < limit:
        return None
    return bookmark


def href_next_cursor(resp: dict) -> Optional[str]:
    """Connections-style: {resources: [], first: {href}, total_count}.

    TODO(spec/02-tools.md, unconfirmed): only `first.href` was observed live;
    no `next` link was confirmed to exist on this endpoint. Defensively check
    a couple of plausible HAL-style key names, but treat this as unverified —
    it may simply not paginate past the first page via a `next` link at all.
    """
    for key in ("next", "next_page"):
        link = resp.get(key)
        if isinstance(link, dict) and link.get("href"):
            return str(link["href"])
        if isinstance(link, str):
            return link
    return None


def split_href(href: str, base_url: str) -> tuple[str, dict]:
    """Split a previously-observed href cursor back into (path, query params) for replay."""
    parsed = urlsplit(href)
    path = parsed.path
    if base_url and href.startswith(base_url):
        path = href[len(base_url) :]
        parsed = urlsplit(path)
        path = parsed.path
    params = dict(pair.split("=", 1) for pair in parsed.query.split("&") if "=" in pair) if parsed.query else {}
    return path, params


def first_present_list(resp: Optional[dict], keys: tuple[str, ...] = ("resources", "results", "items")) -> list:
    """Defensively pull the item array out of a response whose exact envelope key
    for this endpoint isn't pinned down in the spec (jobs, job runs, etc.)."""
    if not resp:
        return []
    for key in keys:
        value = resp.get(key)
        if isinstance(value, list):
            return value
    return []
