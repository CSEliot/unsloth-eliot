# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""Web extraction for agents: ``POST /extract`` turns up to a handful of URLs into readable text.

The work is done by ``_fetch_page_text`` (core/inference/tools.py), the engine behind Studio's own
``web_search`` URL mode, unchanged: it owns the SSRF pinning, redirect policy, encoding handling, PDF
text and HTML to Markdown conversion. This module only decides how many URLs a caller may ask for,
how the character budget is split, how many fetches may be in flight, and how the engine's failure
strings are reported per URL.
"""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.authentication import get_current_subject
from core.inference.tools import _MAX_PAGE_CHARS, _fetch_page_text
from loggers import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read an int env override, never below ``minimum``; fall back to ``default`` on unset/garbage.

    The floor is not cosmetic: every limit below is a knob a deployment may set, and a value under
    the floor turns a working route into a permanent refusal (a 0-sized pool, or fewer in-flight
    slots than one call needs).
    """
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


# The engine appends this notice *after* slicing the text at the cap it is given
# (core/inference/tools.py `_truncate_page_text`), so a page cut to the cap arrives that much
# longer. Every share is reduced by the longest notice the format can produce: the fixed text plus
# room for a 20-digit total, far beyond any page that could be fetched. That keeps the returned text
# inside the share the caller was promised.
_TRUNCATION_NOTICE = "\n\n... (truncated, {} chars total)"
TRUNCATION_NOTICE_RESERVE = len(_TRUNCATION_NOTICE.format("9" * 20))

# The smallest per-page cap a caller may ask for, here and in the MCP tool (studio_mcp/tools/web.py
# MIN_EXTRACT_CHARS, which repeats it because a tool module may not import a route module). A cap
# close to the notice's own length is where the reserve stops helping: at 36 the engine slices to
# nothing and then appends 36 characters, returning text longer than the cap it was given, and
# 37..51 erases almost the whole cap. 64 is the first round number that leaves a page budget worth
# returning (13 characters after the reserve) and is honoured exactly, so the schemas refuse
# everything under it instead of silently breaching their own description.
MIN_EXTRACT_PAGE_CHARS = 64

# Every limit is a developer knob, overridable per installation so a slow or rate-limited site can be
# worked around without a code change. The names are all UNSLOTH_STUDIO_WEB_EXTRACT_*.
#
# The most URLs one call may ask for. Each one is a fresh outbound socket, so this also bounds the
# worst case of a single call.
MAX_EXTRACT_URLS = _env_int("UNSLOTH_STUDIO_WEB_EXTRACT_MAX_URLS", 5)
# Characters per URL, since a megabyte "URL" is never a page anyone wants to read.
MAX_EXTRACT_URL_CHARS = _env_int("UNSLOTH_STUDIO_WEB_EXTRACT_URL_CHARS", 8192)
# The whole call's character budget, split evenly across its URLs, so five pages cannot cost five
# times what one page costs in agent context. 1 URL gets MAX_EXTRACT_PAGE_CHARS, 2 get half the
# budget each, and so on.
MAX_EXTRACT_TOTAL_CHARS = _env_int("UNSLOTH_STUDIO_WEB_EXTRACT_TOTAL_CHARS", 20_000)
# One page's cap, defaulting to the engine's own (tools.py _MAX_PAGE_CHARS). The engine truncates to
# whatever cap it is handed rather than to that constant (it only uses the constant when max_chars
# is None), so the min() in _page_budget is what holds the 16000 default, and raising this knob
# really does raise the page size up to the call's total budget. Floored at the caller minimum,
# because a page cap under it would put the default path back into the overrun the minimum exists to
# refuse: the notice is appended past a cap that is too close to the notice's own length.
MAX_EXTRACT_PAGE_CHARS = _env_int(
    "UNSLOTH_STUDIO_WEB_EXTRACT_PAGE_CHARS", _MAX_PAGE_CHARS, minimum = MIN_EXTRACT_PAGE_CHARS
)
# Own pool, so a slow site cannot starve chat inference's default executor (routes/browser.py does
# the same for the browser panel).
EXTRACT_POOL_SIZE = _env_int("UNSLOTH_STUDIO_WEB_EXTRACT_POOL_SIZE", 8)
# Fetches queued plus running, held until the worker ends. Sized well above the pool so ordinary
# bursts queue rather than fail, and a caller that still cannot get its slots is refused with a 503.
# Floored at MAX_EXTRACT_URLS because one call takes one slot per URL and takes all of them or none:
# a smaller cap would make every 5-URL call a permanent 503.
# One cap for the whole installation, with no per-account share: a single caller can hold every slot
# for up to EXTRACT_DEADLINE_S and 503 everyone else. That is a known first-pass limit, not an
# oversight; a per-account counter is the next step.
EXTRACT_IN_FLIGHT = _env_int(
    "UNSLOTH_STUDIO_WEB_EXTRACT_IN_FLIGHT", 24, minimum = MAX_EXTRACT_URLS
)
# The whole call, including queueing. Each URL's own fetch keeps the engine's 30 s timeout, so this
# only bites when the pool is busy or several URLs are slow at once.
EXTRACT_DEADLINE_S = _env_int("UNSLOTH_STUDIO_WEB_EXTRACT_DEADLINE_S", 60)
EXTRACT_URL_TIMEOUT_S = _env_int("UNSLOTH_STUDIO_WEB_EXTRACT_URL_TIMEOUT_S", 30)

_FETCH_POOL = ThreadPoolExecutor(max_workers = EXTRACT_POOL_SIZE, thread_name_prefix = "web-extract")
_FETCH_SLOTS = threading.BoundedSemaphore(EXTRACT_IN_FLIGHT)

# The engine reports failures as its returned string, never as an exception, and every failure starts
# with one of these. ``(PDF `` is a failed extraction only: a PDF that parsed comes back as its text
# or as text with a ``... (PDF extraction ...)`` suffix, so the prefix never matches a success. The
# bot-check message is a ``Failed to fetch URL: `` string with a sentence appended, so it is covered
# by the first prefix.
#
# The resolver and the content decoder report outside that wrapper, so they need their own entries;
# leaving them out hands a dead hostname or a corrupt gzip body to the agent as page text.
_FAILURE_PREFIXES = (
    "Failed to fetch URL: ",
    # tools.py _RESOLVE_FAILED. A typo'd or dead domain is the likeliest per-URL failure there is.
    "Failed to resolve host",
    "Blocked: ",
    # _read_capped_body's two, returned through _fetch_url_raw without a wrapper.
    "Failed to decode the compressed response: ",
    "(compressed content exceeds ",
    # Only reachable through _fetch_url_raw's raw_bytes_max path, which this route never takes; kept
    # so a future byte cap cannot turn that refusal into page text either.
    "(content exceeds ",
    "(non-text content",
    "(binary content",
    "(PDF ",
    "(page returned no readable text",
)


# One URL string: bounded, because a megabyte "URL" is never a page anyone wants to read.
ExtractUrl = Annotated[str, Field(min_length = 1, max_length = MAX_EXTRACT_URL_CHARS)]


class ExtractRequest(BaseModel):
    urls: Annotated[list[ExtractUrl], Field(min_length = 1, max_length = MAX_EXTRACT_URLS)]
    # Per page, before the call's own total budget is applied. Under the minimum the engine's notice
    # would be appended past a cap this close to the notice's length, returning more than asked for.
    max_chars: Annotated[Optional[int], Field(ge = MIN_EXTRACT_PAGE_CHARS)] = None


class ExtractPage(BaseModel):
    url: str
    text: str = ""
    error: Optional[str] = None


class ExtractResponse(BaseModel):
    results: list[ExtractPage]


def _page_budget(count: int, max_chars: Optional[int]) -> int:
    """The cap to hand the engine for one page: the caller's cap (or the page cap), further split by
    the call's total budget, less the truncation notice the engine appends past that cap.

    Never zero. Every accepted value is at least MIN_EXTRACT_PAGE_CHARS, which exceeds the reserve,
    so the floor only guards a deployment that lowered the page knob by more than the floors allow.
    """
    share = MAX_EXTRACT_TOTAL_CHARS // max(count, 1)
    cap = min(max_chars or MAX_EXTRACT_PAGE_CHARS, MAX_EXTRACT_PAGE_CHARS, share)
    return max(1, cap - TRUNCATION_NOTICE_RESERVE)


def _failed(text: str) -> Optional[str]:
    """The engine's failure string, or None when the string is page text."""
    for prefix in _FAILURE_PREFIXES:
        if text.startswith(prefix):
            return text
    return None


def _fetch_one(url: str, max_chars: int, slots: threading.BoundedSemaphore) -> dict:
    """One URL on a pool thread, releasing its slot whatever happens. The engine does not raise, but a
    surprise there must not take the whole call down or leak a slot."""
    try:
        text = _fetch_page_text(url, max_chars = max_chars, timeout = EXTRACT_URL_TIMEOUT_S)
    except Exception as exc:
        logger.debug("web extract failed (%s)", type(exc).__name__)
        return {"url": url, "text": "", "error": f"Failed to fetch URL: {type(exc).__name__}."}
    finally:
        slots.release()
    error = _failed(text)
    if error is not None:
        return {"url": url, "text": "", "error": error}
    return {"url": url, "text": text, "error": None}


def _drain(task) -> None:
    # A fetch left behind by the call deadline still finishes its socket work; its exception (or its
    # cancellation) must not be reported as unretrieved.
    if not task.cancelled():
        task.exception()


@router.post("/extract", response_model = ExtractResponse)
async def web_extract(
    request: ExtractRequest,
    current_subject: str = Depends(get_current_subject),
):
    """Read up to five URLs as plain text or Markdown. Failures are per URL, in the same 200."""
    urls = request.urls
    budget = _page_budget(len(urls), request.max_chars)
    # One slot per URL for the whole fetch, queued or running, so a burst of calls cannot pile sockets
    # up behind slow sites. All of a call's slots or none of them: a half-admitted call would report
    # errors the caller could not tell from real ones.
    held = 0
    for _ in urls:
        if not _FETCH_SLOTS.acquire(blocking = False):
            for _ in range(held):
                _FETCH_SLOTS.release()
            raise HTTPException(
                status_code = 503, detail = "Too many web fetches in flight; retry shortly."
            )
        held += 1
    loop = asyncio.get_running_loop()
    tasks = []
    try:
        for url in urls:
            tasks.append(loop.run_in_executor(_FETCH_POOL, _fetch_one, url, budget, _FETCH_SLOTS))
    except BaseException:
        # No worker will ever release the slots of the URLs that were never submitted.
        for _ in range(held - len(tasks)):
            _FETCH_SLOTS.release()
        raise

    _done, pending = await asyncio.wait(tasks, timeout = EXTRACT_DEADLINE_S)
    for task in pending:
        task.add_done_callback(_drain)
    results = []
    for url, task in zip(urls, tasks):
        if task in pending:
            results.append({"url": url, "text": "", "error": "Failed to fetch URL: timed out."})
        else:
            results.append(task.result())
    return {"results": results}
