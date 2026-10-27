# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present The Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""``web_extract``: forwards one call, keeps the order and the per-URL errors."""

from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

from routes.web import MIN_EXTRACT_PAGE_CHARS
from studio_mcp.tools.web import MIN_EXTRACT_CHARS

from .mcp_harness import call_to, run_tool

EXTRACT = ("POST", "/api/web/extract")
URLS = ["https://example.com/a", "https://example.com/b"]


def test_the_page_minimum_matches_the_route():
    # The tool module may not import a route module, so the bound exists twice; the route is the
    # authoritative copy and this is what keeps the two from drifting apart.
    assert MIN_EXTRACT_CHARS == MIN_EXTRACT_PAGE_CHARS


def _results(*rows: dict) -> dict:
    return {"results": list(rows)}


def _page(url: str, text: str = "", error = None) -> dict:
    return {"url": url, "text": text, "error": error}


def test_the_call_is_forwarded_with_its_urls_in_order(monkeypatch):
    payload = _results(_page(URLS[0], "# A"), _page(URLS[1], "# B"))
    result, studio = run_tool(monkeypatch, {EXTRACT: payload}, "web_extract", {"urls": URLS})
    assert result["isError"] is False
    assert result["structuredContent"]["pages"] == [
        {"url": URLS[0], "text": "# A", "error": None},
        {"url": URLS[1], "text": "# B", "error": None},
    ]
    method, path, headers, body = call_to(studio, "/api/web/extract")
    assert (method, path) == ("POST", "/api/web/extract")
    assert json.loads(body) == {"urls": URLS}
    assert headers["content-type"] == "application/json"


def test_max_chars_goes_with_the_call(monkeypatch):
    payload = _results(_page(URLS[0], "short"))
    result, studio = run_tool(
        monkeypatch, {EXTRACT: payload}, "web_extract", {"urls": [URLS[0]], "max_chars": 900}
    )
    assert result["isError"] is False
    assert json.loads(call_to(studio, "/api/web/extract")[3]) == {
        "urls": [URLS[0]],
        "max_chars": 900,
    }


def test_a_page_error_is_kept_and_the_other_pages_still_come_back(monkeypatch):
    payload = _results(
        _page(URLS[0], error = "Failed to fetch URL: HTTP 404 Not Found"),
        _page(URLS[1], "# B"),
    )
    result, _studio = run_tool(monkeypatch, {EXTRACT: payload}, "web_extract", {"urls": URLS})
    assert result["isError"] is False
    first, second = result["structuredContent"]["pages"]
    assert first == {"url": URLS[0], "text": "", "error": "Failed to fetch URL: HTTP 404 Not Found"}
    assert second == {"url": URLS[1], "text": "# B", "error": None}


@pytest.mark.parametrize(
    "urls",
    [[], ["https://example.com/"] * 6],
    ids = ["empty", "six-urls"],
)
def test_a_refused_list_never_reaches_the_route(monkeypatch, urls):
    # Every route answers, so any call the tool made would be recorded.
    result, studio = run_tool(monkeypatch, {}, "web_extract", {"urls": urls}, fallback = {})
    assert result["isError"] is True
    assert studio.state.calls == []


def test_a_route_failure_is_a_tool_error_with_its_message(monkeypatch):
    refused = JSONResponse({"detail": "urls may hold at most 5 entries"}, status_code = 400)
    result, _studio = run_tool(
        monkeypatch, {EXTRACT: refused}, "web_extract", {"urls": [URLS[0]]}
    )
    assert result["isError"] is True
    message = result["content"][0]["text"]
    assert "urls may hold at most 5 entries" in message
    assert "(HTTP 400)" in message
