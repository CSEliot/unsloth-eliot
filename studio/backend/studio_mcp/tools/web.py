# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""``web_extract``: read web pages as text, through Studio's own extraction route and engine.
``web_browse``: read one page with JavaScript run, in the desktop app's own browser panel."""

from __future__ import annotations

import asyncio
from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from studio_mcp import profile
from studio_mcp.outputs import WebBrowseResult, WebExtractResult, WebPage
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


# The browser panel's waits, in seconds. A navigation is a fetch of up to 25 s plus settling, so it
# gets the longer one; a snapshot only reads the page already open. Read by name inside the calls,
# not bound as defaults, so a test can shorten them.
NAVIGATE_WAIT_S = 40.0
SNAPSHOT_WAIT_S = 15.0
MAX_BROWSE_CHARS = 16000
# The browser's own address bound (browser_agent._MAX_URL_CHARS), repeated so the schema refuses an
# over-long address instead of the panel's validator doing it. A tool module may not import the
# inference core at import time (no other studio_mcp tool does: the MCP server's import path would
# carry the whole engine), so the two are kept in step here.
MAX_BROWSE_URL_CHARS = 4096
# Under this the truncation notice alone fills the answer, so it is refused rather than returned
# longer than the caller asked for; routes/web.py keeps the same floor for the same reason.
MIN_BROWSE_CHARS = 64

# web_extract's own sentence for a page it fetched and found no words on (core/inference/tools.py
# _truncate_page_text), repeated here because a panel that answers with no text has read the same
# nothing. Returning the Page line alone would read as a successful empty page instead.
NO_PAGE_TEXT = "(page returned no readable text)"
# What the panel answers when a loaded page has no text yet. A client-rendered app is exactly this
# at the moment its navigation completes, so the retries below exist to look again as it paints.
NO_PAGE_YET = "(nothing readable here)"
SNAPSHOT_RETRY_DELAYS_S = (0.8, 2.5)

# No claimer within the wait means there is no panel to ask: the app is closed, its browser panel is
# not running, or the Browser tool is turned off for chats, which stops the panel listening for these.
# Said plainly and as an ordinary result, because an agent that asked a question of a closed app has
# not failed at anything it can fix by asking again differently.
NOT_OPEN = (
    "The Unsloth Studio desktop app is not open, its browser panel is not running, or the Browser "
    "tool is off for chats, so there is no browser to read the page in. Open the Unsloth Studio "
    "desktop app, turn on the Browser tool and show the browser panel, then ask again."
)


async def web_browse(
    url: Annotated[
        str,
        Field(
            min_length = 1,
            max_length = MAX_BROWSE_URL_CHARS,
            description = "The http(s) page to read, at most 4096 characters.",
        ),
    ],
    max_chars: Annotated[
        int,
        Field(
            ge = MIN_BROWSE_CHARS,
            le = MAX_BROWSE_CHARS,
            description = "Characters to return, at least 64 and at most 16000 (default 16000).",
        ),
    ] = MAX_BROWSE_CHARS,
) -> WebBrowseResult:
    """Read one web page with JavaScript run, in Unsloth Studio's own desktop app.

    The Unsloth Studio desktop app renders the page in one of its own browser views, so a site that
    builds itself in the browser comes back readable where web_extract returns an empty page. That
    view is the app's background browser, not the panel beside the chat: the page is not shown, and
    nobody watches it load. The page is read under the user's own browser permissions, unchanged:
    a site the user has not allowed asks its question in Studio's browser panel, and this call waits
    for an answer it cannot give.
    The desktop app must be open, with the Browser tool on for chats and its browser panel reachable;
    otherwise the navigation times out after about 40 seconds and this returns a clear sentence
    saying the app or its panel is not running, instead of a page. A page that is still painting is
    read again up to twice, so a call can take about 73 seconds at worst.
    The text comes back inside an untrusted-content fence, cut to max_chars with a notice when it is
    longer, and the fence and its note survive that cut: a cap too small to hold them returns them
    with no page text at all rather than a fence left open. A page with no readable text says so, as
    web_extract does; a page the panel refuses to open is an error naming the reason.
    """
    from core.inference.browser_agent import _validated_request, format_result

    policy = _website_policy()
    try:
        # Off the loop: the address check resolves the host, and a slow resolver must not stall every
        # other MCP call and Studio request for as long as the lookup takes.
        request = await asyncio.to_thread(
            _validated_request, "browser_navigate", {"url": url}, policy
        )
    except ValueError as exc:
        # A local, non-public or policy-blocked address is refused here, before any panel sees it.
        raise ToolError(str(exc)) from None
    if "url" not in request:
        raise ToolError('url must be a full http(s) address, not "back", "forward" or "reload".')
    result = await _ask_panel(request, policy, NAVIGATE_WAIT_S)
    if result is None:
        # Not cut to max_chars: a fixed sentence the caller has to be able to read whole beats a cap.
        return WebBrowseResult(url = url, text = NOT_OPEN)
    formatted = format_result("browser_navigate", result, policy)
    if not formatted.startswith("Error: ") and not _has_words(_page_text(result)):
        # Client-rendered pages (Blazor, React, anything that boots after the load event) paint their
        # text after the navigation reports success, so an immediate read can only see the shell.
        # Ask again as it fills, with the delays growing, then accept that the page is empty.
        last_error = ""
        for delay in SNAPSHOT_RETRY_DELAYS_S:
            await asyncio.sleep(delay)
            snapshot = await _ask_panel(
                _validated_request("browser_snapshot", {}, policy), policy, SNAPSHOT_WAIT_S
            )
            if snapshot is None:
                last_error = NOT_OPEN
                continue
            if snapshot.get("ok") is not True:
                last_error = str(snapshot.get("error") or NO_PAGE_TEXT)
                continue
            if _has_words(_page_text(snapshot)):
                result = snapshot
                break
        else:
            # A read that was refused or never answered is an error, not an empty page: reporting
            # "nothing to read" would tell the model the site is empty when it was never read.
            if last_error:
                raise ToolError(last_error)
            # The panel did answer, with nothing to read: an empty page is its own answer, and one
            # that says so is worth more to the model than a bare Page line it must interpret.
            result = {**result, "text": NO_PAGE_TEXT}
        formatted = format_result("browser_navigate", result, policy)
    if formatted.startswith("Error: "):
        raise ToolError(formatted.removeprefix("Error: "))
    return WebBrowseResult(url = url, text = _fenced_page(result, policy, max_chars))


def _website_policy() -> dict:
    """The website policy this call runs under, normalized by the same function the chat path uses.

    An agent on /mcp has no chat whose declared policy could apply, and Studio stores no
    installation-wide one, so this is the neutral policy: nothing allowed or blocked here. The
    panel's own gates still run, and format_result withholds a page that ends up on a host its
    checks refuse.
    """
    from core.inference.web_access_policy import normalize_website_policy

    return normalize_website_policy(None)


def _acting_account() -> str:
    """The account whose browser panel may answer this action.

    The /mcp gate records the caller's account but deliberately does not bind it, so
    current_account_id() is the owner even for a managed account's key. The panel polls as the
    account signed in to the app, so the action has to carry the caller's: the owner's agent is
    answered by the owner's app, and a managed account's only by that account's.
    """
    from studio_mcp.caller import current_caller
    from utils.account_context import current_account_id

    try:
        return current_caller().account_id or current_account_id()
    except RuntimeError:
        # No caller at all (an in-process call outside /mcp): the bound account is all there is.
        return current_account_id()


async def _ask_panel(request: dict, policy: dict, wait: float) -> Optional[dict]:
    """Announce one request for the browser panel to claim, and return its answer or None.

    This is the round trip a chat's browser tool makes, with a different answerer: a chat tab through
    its stream, the desktop app's panel through GET /api/browser/actions/claim. The wait happens in a
    worker thread because the answer arrives on a slot this call blocks on, and blocking the event
    loop would stall every other MCP call and Studio request for up to the wait bound.
    """
    from studio_mcp.action_payload import action_payload
    from state.browser_actions import (
        announce_browser_action,
        begin_browser_action,
        discard_browser_action,
        new_action_id,
        wait_browser_action,
    )

    action_id = new_action_id()
    slot = begin_browser_action(action_id, "", _acting_account())
    announce_browser_action(action_id, slot, action_payload(action_id, "", request, policy, wait))
    try:
        outcome, result = await asyncio.to_thread(wait_browser_action, slot, action_id, None, wait)
    except BaseException:
        # A cancelled or failed call must not leave an action a panel would still run.
        discard_browser_action(action_id, slot)
        raise
    return result if outcome == "answered" and isinstance(result, dict) else None


def _page_text(result: dict) -> str:
    text = result.get("text")
    return text if isinstance(text, str) and text.strip() else ""


_MIN_PAGE_CHARS = 40


def _has_words(text: str) -> bool:
    """Whether the panel's read has enough page in it to be a page rather than a shell.

    A client-rendered app that has not painted reads as a position note and the panel's
    "(nothing readable here)", and a "Loading…" splash is a handful of characters. Both are answers
    about a moment, not about the page, so the test is how much real text there is, not whether it
    matches one placeholder: a splash nobody listed still retries, and the position note does not
    count as content."""
    kept: list[str] = []
    for line in text.splitlines():
        word = line.strip()
        if not word or word in (NO_PAGE_YET, NO_PAGE_TEXT) or word.startswith("Page: "):
            continue
        if word.startswith("Showing ") and "scroll for more" in word:
            continue
        kept.append(word)
    return len(" ".join(kept)) >= _MIN_PAGE_CHARS


def _fenced_page(result: dict, policy: dict, max_chars: int) -> str:
    """The page as the model reads it, with its words cut to ``max_chars`` before the fence is built.

    ``format_result`` wraps the page in ``<untrusted_web_content>`` and follows it with the note that
    says the content is untrusted. Formatting first and clipping after would cut the closing tag and
    the note off any page longer than the cap, leaving an unterminated fence. Formatting the same
    result without its text measures exactly what the fence, the page line and the note cost; the
    page's own words get what is left, so the cap holds whenever it can hold the envelope at all.
    """
    from core.inference.browser_agent import format_result

    head = format_result("browser_navigate", {**result, "text": ""}, policy)
    # The page's text is joined onto the header lines with one newline.
    room = max_chars - len(head) - 1
    return format_result(
        "browser_navigate", {**result, "text": _clip(_page_text(result), room)}, policy
    )


def _clip(text: str, max_chars: int) -> str:
    """``text`` cut to ``max_chars``, with the notice web_extract's engine appends. The notice is
    counted in the cap, so text never comes back longer than the caller asked for. A cap with no
    room for the notice leaves no text at all: half a sentence and no notice says less than the
    fence around the page line already does."""
    if max_chars <= 0 or not text:
        return ""
    if len(text) <= max_chars:
        return text
    notice = f"\n\n... (truncated, {len(text)} chars total)"
    if len(notice) >= max_chars:
        return ""
    return text[: max_chars - len(notice)] + notice


# Reaches the open internet, so it is read only in the sense that it changes nothing here, and open
# world unlike every other tool: READ_ONLY would claim it acts on this Studio only.
READ_ONLY_WEB = ToolAnnotations(readOnlyHint = True, openWorldHint = True)

TOOLS = (
    (web_extract, READ_ONLY_WEB, WebExtractResult.model_json_schema()),
    (web_browse, READ_ONLY_WEB, WebBrowseResult.model_json_schema()),
)

# A chat may not be talked into pulling a page into its own context: agents on /mcp keep it, chats do
# not. web_browse carries the same fact for the same reason: a prompt could otherwise name a page and
# have the chat open it in a panel it then reads from.
FACTS = {
    "web_extract": {profile.FETCHES_OPEN_WORLD},
    "web_browse": {profile.FETCHES_OPEN_WORLD},
}
