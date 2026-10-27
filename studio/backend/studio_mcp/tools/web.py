# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""``web_extract``: read web pages as text, through Studio's own extraction route and engine."""

from __future__ import annotations

from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from studio_mcp.outputs import WebExtractResult, WebPage
from studio_mcp.tools import route_json

MAX_EXTRACT_URLS = 5
MAX_EXTRACT_CHARS = 16000
# The route's own per-URL bound (routes/web.py MAX_EXTRACT_URL_CHARS), repeated here so the two
# schemas agree instead of letting a 9,000-character URL through to a 422. A tool module may not
# import a route module (no other studio_mcp tool does: it would pull FastAPI routing and auth into
# the MCP server's import path), so the route's value is the authoritative one and this copy has to
# match it. A deployment that lowers the env knob still gets a 422 the tool forwards as a ToolError.
MAX_EXTRACT_URL_CHARS = 8192
# routes/web.py MIN_EXTRACT_PAGE_CHARS, repeated for the same reason: a cap close to the engine's
# truncation notice comes back longer than asked for, so both schemas refuse under 64.
MIN_EXTRACT_CHARS = 64


async def web_extract(
    urls: Annotated[
        list[Annotated[str, Field(min_length = 1, max_length = MAX_EXTRACT_URL_CHARS)]],
        Field(
            min_length = 1,
            max_length = MAX_EXTRACT_URLS,
            description = (
                "http(s) URLs to read, in the order you want them back. At most 5, each at most"
                " 8192 characters."
            ),
        ),
    ],
    max_chars: Annotated[
        Optional[int],
        Field(
            ge = MIN_EXTRACT_CHARS,
            le = MAX_EXTRACT_CHARS,
            description = "Characters per page, at least 64 and at most 16000 (default 16000, or less when the call's budget is split).",
        ),
    ] = None,
) -> WebExtractResult:
    """Read up to 5 web pages and return each one's text, in the order asked for.
    HTML comes back as Markdown; plain text, JSON and a PDF's extracted text come back as they are.
    No JavaScript runs, so a site that builds its page in the browser returns no readable text, and asking again will not help.
    A call may return up to 20000 characters in total, split across its URLs: 1 URL gets up to
    16000, 2 get up to 10000 each, 5 get up to 4000 each. Pass max_chars for less per page, at least
    64: a smaller cap cannot hold the truncation notice, so it is refused rather than returned longer
    than asked for. These are ceilings, not quotas: a page shorter than its share comes back whole,
    and a model loaded in Unsloth Studio with a small context window shrinks every page further to
    fit it, so expect less when one is loaded.
    Errors are per URL: that page's error is set and its text is empty, while the other pages still come back.
    Each result's url is the one you asked for; redirects and meta refresh are followed but not reported.
    """
    body: dict = {"urls": urls}
    if max_chars is not None:
        body["max_chars"] = max_chars
    payload = await route_json("POST", "/api/web/extract", json_body = body)
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(urls):
        raise ToolError("Unsloth Studio returned no page for one of the URLs")
    pages = []
    for url, row in zip(urls, rows):
        row = row if isinstance(row, dict) else {}
        page_url = row.get("url")
        page_text = row.get("text")
        page_error = row.get("error")
        pages.append(
            WebPage(
                url = page_url if isinstance(page_url, str) and page_url else url,
                text = page_text if isinstance(page_text, str) else "",
                error = page_error if isinstance(page_error, str) else None,
            )
        )
    return WebExtractResult(pages = pages)


# Reaches the open internet, so it is read only in the sense that it changes nothing here, and open
# world unlike every other tool: READ_ONLY would claim it acts on this Studio only.
READ_ONLY_WEB = ToolAnnotations(readOnlyHint = True, openWorldHint = True)

TOOLS = ((web_extract, READ_ONLY_WEB, WebExtractResult.model_json_schema()),)
