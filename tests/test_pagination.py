"""Pagination codecs for the three confirmed native shapes — spec/03-conventions.md."""

from __future__ import annotations

from watsonx_platform_mcp.pagination import (
    bookmark_next_cursor,
    clamp_limit,
    decode_json_cursor,
    encode_json_cursor,
    first_present_list,
    href_next_cursor,
)


def test_clamp_limit_defaults_and_bounds():
    assert clamp_limit(None) == 50
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(9999) == 200
    assert clamp_limit(75) == 75


def test_bookmark_cursor_full_page_propagates_bookmark():
    resources = [{"id": i} for i in range(50)]
    assert bookmark_next_cursor(resources, "bm-token", 50) == "bm-token"


def test_bookmark_cursor_short_page_means_last_page():
    resources = [{"id": 1}]
    assert bookmark_next_cursor(resources, "bm-token", 50) is None


def test_json_cursor_round_trips_arbitrary_object():
    """Confirmed live: the asset-search `next` object must be echoed back
    verbatim, not just a bookmark string."""
    next_obj = {"query": "*:*", "limit": 50, "bookmark": "abc", "skip": 50, "include_total_rows": True}
    cursor = encode_json_cursor(next_obj)
    assert decode_json_cursor(cursor) == next_obj


def test_href_next_cursor_defensive_lookup():
    assert href_next_cursor({"next": {"href": "/v2/connections?bookmark=x"}}) == "/v2/connections?bookmark=x"
    assert href_next_cursor({"first": {"href": "/v2/connections"}}) is None  # only `first` observed live, no `next`
    assert href_next_cursor({}) is None


def test_first_present_list_checks_multiple_shapes():
    assert first_present_list({"resources": [1, 2]}) == [1, 2]
    assert first_present_list({"results": [3]}) == [3]
    assert first_present_list({"nothing": []}) == []
    assert first_present_list(None) == []
