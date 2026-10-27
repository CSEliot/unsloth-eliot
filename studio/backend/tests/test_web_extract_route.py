# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""``POST /api/web/extract``: one text per URL, failures reported per URL, one budget and one pool."""

from __future__ import annotations

import ast
import importlib
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.authentication import get_current_subject
from core.inference import tools as engine
from core.inference import web_access_policy as policy
import routes.web as web_mod

ENDPOINT = "/api/web/extract"


def _app(authenticated = True) -> FastAPI:
    app = FastAPI()
    app.include_router(web_mod.router, prefix = "/api/web")
    if authenticated:
        app.dependency_overrides[get_current_subject] = lambda: "unsloth"
    return app


@pytest.fixture
def client():
    with TestClient(_app()) as http:
        yield http


def _engine(monkeypatch, pages = None, delays = None):
    """Replace the engine the route imported. Records each call and returns ``pages[url]``, else text."""
    seen: list[dict] = []
    lock = threading.Lock()

    def fetch(url, max_chars = None, timeout = None):
        with lock:
            seen.append(
                {
                    "url": url,
                    "max_chars": max_chars,
                    "timeout": timeout,
                    "thread": threading.current_thread().name,
                    "thread_id": threading.get_ident(),
                }
            )
        if delays and url in delays:
            time.sleep(delays[url])
        return (pages or {}).get(url, f"# {url}")

    monkeypatch.setattr(web_mod, "_fetch_page_text", fetch)
    return seen


def _urls(count: int) -> list[str]:
    return [f"https://example.com/page-{index}" for index in range(count)]


def _share(count: int) -> int:
    """The characters one page may return in a call of ``count`` URLs, notice included."""
    return min(web_mod.MAX_EXTRACT_PAGE_CHARS, web_mod.MAX_EXTRACT_TOTAL_CHARS // count)


def _engine_cap(count: int) -> int:
    """The cap the route must hand the engine: the share less the truncation notice reserve."""
    return max(1, _share(count) - web_mod.TRUNCATION_NOTICE_RESERVE)


def _slots_left(slots: threading.BoundedSemaphore) -> int:
    """Slots still free in ``slots``, read rather than taken: taking one races the release."""
    return slots._value


def test_no_key_is_refused():
    with TestClient(_app(authenticated = False)) as http:
        response = http.post(ENDPOINT, json = {"urls": ["https://example.com/a"]})
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_two_authorization_headers_are_refused(monkeypatch):
    # A caller cannot pick which of two keys the route ran as.
    seen = _engine(monkeypatch)
    with TestClient(_app(authenticated = False)) as http:
        response = http.post(
            ENDPOINT,
            json = {"urls": ["https://example.com/a"]},
            headers = [("Authorization", "Bearer a"), ("Authorization", "Bearer b")],
        )
    assert response.status_code == 403
    assert seen == []


def test_an_invalid_key_is_refused(monkeypatch):
    seen = _engine(monkeypatch)
    with TestClient(_app(authenticated = False)) as http:
        response = http.post(
            ENDPOINT,
            json = {"urls": ["https://example.com/a"]},
            headers = {"Authorization": "Bearer not-a-key"},
        )
    assert response.status_code == 401
    assert seen == []


@pytest.mark.parametrize(
    "body",
    [
        {"urls": []},
        {"urls": _urls(6)},
        {"urls": ["https://example.com/" + "a" * 9000]},
        {"urls": ["https://example.com/a"], "max_chars": 0},
        # Under the minimum the engine's notice is appended past the cap, so it is refused here
        # instead of returning more text than the caller asked for.
        {"urls": ["https://example.com/a"], "max_chars": 63},
    ],
    ids = ["no-urls", "six-urls", "over-long-url", "zero-chars", "under-min-chars"],
)
def test_a_bad_request_is_a_422_and_fetches_nothing(client, monkeypatch, body):
    seen = _engine(monkeypatch)
    assert client.post(ENDPOINT, json = body).status_code == 422
    assert seen == []


ENV_TUNABLES = (
    "UNSLOTH_STUDIO_WEB_EXTRACT_MAX_URLS",
    "UNSLOTH_STUDIO_WEB_EXTRACT_URL_CHARS",
    "UNSLOTH_STUDIO_WEB_EXTRACT_TOTAL_CHARS",
    "UNSLOTH_STUDIO_WEB_EXTRACT_PAGE_CHARS",
    "UNSLOTH_STUDIO_WEB_EXTRACT_POOL_SIZE",
    "UNSLOTH_STUDIO_WEB_EXTRACT_IN_FLIGHT",
    "UNSLOTH_STUDIO_WEB_EXTRACT_DEADLINE_S",
    "UNSLOTH_STUDIO_WEB_EXTRACT_URL_TIMEOUT_S",
)


def test_env_tunables_are_clamped_to_a_usable_minimum(monkeypatch):
    # A knob under its floor is not a smaller limit but a permanent refusal: a 2-slot in-flight cap
    # can never admit a 5-URL call, and a 0-sized pool never runs one.
    for name, value in (
        ("MAX_URLS", "5"),
        ("IN_FLIGHT", "2"),
        ("POOL_SIZE", "0"),
        ("TOTAL_CHARS", "-100"),
        ("PAGE_CHARS", "0"),
        ("DEADLINE_S", "0"),
        ("URL_TIMEOUT_S", "-1"),
        ("URL_CHARS", "0"),
    ):
        monkeypatch.setenv(f"UNSLOTH_STUDIO_WEB_EXTRACT_{name}", value)
    try:
        reloaded = importlib.reload(web_mod)
        assert reloaded.MAX_EXTRACT_URLS == 5
        assert reloaded.EXTRACT_IN_FLIGHT == reloaded.MAX_EXTRACT_URLS
        for constant in (
            reloaded.MAX_EXTRACT_URL_CHARS,
            reloaded.MAX_EXTRACT_TOTAL_CHARS,
            reloaded.MAX_EXTRACT_PAGE_CHARS,
            reloaded.EXTRACT_POOL_SIZE,
            reloaded.EXTRACT_DEADLINE_S,
            reloaded.EXTRACT_URL_TIMEOUT_S,
        ):
            assert constant >= 1
        # And a full-size call still fits the slots it is given.
        assert reloaded._page_budget(reloaded.MAX_EXTRACT_URLS, None) >= 1
    finally:
        # Assumes a clean environment for these names: it drops the overrides and reloads, so a knob
        # exported by the shell is silently left at its default rather than at that exported value.
        for name in ENV_TUNABLES:
            monkeypatch.delenv(name, raising = False)
        importlib.reload(web_mod)


def test_one_url_gets_the_page_cap_and_its_text(client, monkeypatch):
    seen = _engine(monkeypatch)
    response = client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]})
    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {"url": "https://example.com/a", "text": "# https://example.com/a", "error": None}
        ]
    }
    # The page cap less the truncation notice, so the text that comes back still fits 16000.
    assert seen[0]["max_chars"] == _engine_cap(1) == 16000 - web_mod.TRUNCATION_NOTICE_RESERVE
    # Each URL keeps the engine's own 30 s timeout.
    assert seen[0]["timeout"] == 30


@pytest.mark.parametrize("count,per_page", [(1, 16000), (2, 10000), (5, 4000)])
def test_the_total_budget_is_split_across_the_urls(client, monkeypatch, count, per_page):
    seen = _engine(monkeypatch)
    assert client.post(ENDPOINT, json = {"urls": _urls(count)}).status_code == 200
    assert _share(count) == per_page
    assert [call["max_chars"] for call in seen] == [_engine_cap(count)] * count


def test_a_caller_cap_below_the_share_is_honored(client, monkeypatch):
    seen = _engine(monkeypatch)
    body = {"urls": _urls(2), "max_chars": 500}
    assert client.post(ENDPOINT, json = body).status_code == 200
    assert [call["max_chars"] for call in seen] == [500 - web_mod.TRUNCATION_NOTICE_RESERVE] * 2


@pytest.mark.parametrize("max_chars", [64, 65, 100, 1200, 16000])
def test_an_accepted_cap_is_never_exceeded_at_the_minimum(client, monkeypatch, max_chars):
    # The schema's minimum is only worth anything if the engine still gets a positive budget after
    # the notice reserve, and if the notice the engine appends past that budget does not push the
    # page over what the caller asked for. 64 and 65 are the two smallest accepted values.
    assert max_chars >= web_mod.MIN_EXTRACT_PAGE_CHARS
    budget = web_mod._page_budget(1, max_chars)
    assert budget >= 1, (max_chars, budget)

    def fetch(url, max_chars = None, timeout = None):
        # The real engine truncation, no network: its notice is what used to overrun a small cap.
        assert max_chars is not None
        return engine._truncate_page_text("# " + "a" * 40000, max_chars)

    monkeypatch.setattr(web_mod, "_fetch_page_text", fetch)
    body = {"urls": ["https://example.com/a"], "max_chars": max_chars}
    (result,) = client.post(ENDPOINT, json = body).json()["results"]
    assert result["error"] is None
    # Truncated, so the notice is really in the text being measured.
    assert result["text"].endswith("chars total)")
    assert len(result["text"]) <= max_chars, (max_chars, len(result["text"]))


def _truncating_engine(monkeypatch):
    """An engine that cuts at the cap and appends the notice, as the real `_truncate_page_text` does.

    The notice goes on *after* the slice, which is what made a 4,000-character share return 4,036.
    """

    def fetch(url, max_chars = None, timeout = None):
        text = f"# {url}\n" + "a" * 40000
        if max_chars is not None and len(text) > max_chars:
            return text[:max_chars] + f"\n\n... (truncated, {len(text)} chars total)"
        return text

    monkeypatch.setattr(web_mod, "_fetch_page_text", fetch)


@pytest.mark.parametrize("count", [1, 2, 5])
def test_no_page_arrives_over_its_share(client, monkeypatch, count):
    _truncating_engine(monkeypatch)
    results = client.post(ENDPOINT, json = {"urls": _urls(count)}).json()["results"]
    lengths = [len(result["text"]) for result in results]
    # Every page was truncated (the notice is there), so the ceiling is really being tested.
    assert all(result["text"].endswith("chars total)") for result in results)
    assert all(length <= _share(count) for length in lengths), lengths
    assert sum(lengths) <= web_mod.MAX_EXTRACT_TOTAL_CHARS


@pytest.mark.parametrize("count", [1, 2, 5])
def test_the_real_engine_notice_still_fits_the_share(client, monkeypatch, count):
    # The engine's own truncation with no network: the notice's length is what is being pinned, and
    # the route cannot know it from the string it writes here.
    def fetch(url, max_chars = None, timeout = None):
        assert max_chars is not None
        return engine._truncate_page_text("# " + "a" * 40000, max_chars)

    monkeypatch.setattr(web_mod, "_fetch_page_text", fetch)
    results = client.post(ENDPOINT, json = {"urls": _urls(count)}).json()["results"]
    for result in results:
        assert result["error"] is None
        assert result["text"].endswith("chars total)")
        assert len(result["text"]) <= _share(count)


def test_pages_come_back_in_the_order_asked_for(client, monkeypatch):
    urls = _urls(3)
    # The first is the slowest, so completion order is the reverse of input order.
    _engine(monkeypatch, delays = {urls[0]: 0.05, urls[1]: 0.03})
    results = client.post(ENDPOINT, json = {"urls": urls}).json()["results"]
    assert [result["url"] for result in results] == urls


# Each string the engine reports a failure with. The bot-check message is a Failed to fetch URL one
# with a sentence appended, and a PDF that parsed comes back as text, so neither is in this list.
FAILURES = [
    "Failed to fetch URL: HTTP 404 Not Found",
    # tools.py _RESOLVE_FAILED: a typo'd or dead hostname, the commonest real per-URL failure, and
    # the one that used to arrive as page text.
    "Failed to resolve host: no addresses for 'exmaple.invalid'",
    "Failed to resolve host: [Errno -2] Name or service not known",
    # _read_capped_body's two, which _fetch_url_raw returns unwrapped.
    "(compressed content exceeds the 2097152 byte limit)",
    "Failed to decode the compressed response: Error -3 while decompressing data: invalid distance",
    # _fetch_url_raw's byte cap; the route never passes raw_bytes_max, so this is defence only.
    "(content exceeds the 1000 byte limit)",
    "Blocked: refusing to fetch non-public address 10.0.0.1.",
    "(non-text content: image/png, 123 bytes; not readable as text)",
    "(binary content, 123 bytes; not readable as text)",
    "(PDF content could not be read as text)",
    "(page returned no readable text)",
    "Failed to fetch URL: HTTP 403 Forbidden. The site's bot protection blocked this automated "
    "request; fetching this site again will not help, so use another source.",
]

TOOLS_SOURCE = Path(engine.__file__).read_text(encoding = "utf-8")
# The functions on _fetch_page_text's path that can return one of its failure strings.
FAILURE_FUNCTIONS = frozenset(
    {
        "_fetch_page_text",
        "_fetch_url_raw",
        "_read_capped_body",
        "_truncate_page_text",
        "_fetch_budget_exceeded",
        "_resolve_with_budget",
        "_validate_and_resolve_host",
        "_redirect_hop",
    }
)
# check_url_access is on the same path (_fetch_page_text returns its reason verbatim) but lives in
# another module, so it needs its own source and its own name set.
POLICY_SOURCE = Path(policy.__file__).read_text(encoding = "utf-8")
POLICY_FUNCTIONS = frozenset({"check_url_access"})
# A failure string opens with one of these three shapes (tools.py's wrappers, the URL access policy,
# and the parenthesised per-content refusals).
FAILURE_OPENINGS = ("Failed to ", "Blocked: ", "(")


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module level ``NAME = "..."`` strings, so an f-string can be read through its first part."""
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                constants[target.id] = value.value
    return constants


def _scan_failure_literals(source: str, functions: frozenset) -> tuple[list[str], set[str]]:
    """Every string ``functions`` returns as a failure, one literal each, and the names it found.

    Reads the AST rather than a list here, so an upstream reword shows up as a failure instead of a
    route that quietly hands an error string back as page text. A literal counts when it opens with
    one of FAILURE_OPENINGS; an f-string contributes its leading constant, or the module constant
    its first part names (``f"{_RESOLVE_FAILED}: ..."``), which is how the resolver reports.
    """
    tree = ast.parse(source)
    constants = _module_string_constants(tree)
    literals = set()
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in functions:
            continue
        found.add(node.name)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                text = inner.value
            elif isinstance(inner, ast.JoinedStr) and inner.values:
                first = inner.values[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    text = first.value
                elif isinstance(first, ast.FormattedValue):
                    # f"{_RESOLVE_FAILED}: {e}" reads through the module constant it interpolates.
                    name = first.value
                    if isinstance(name, ast.Attribute):
                        name = name.attr
                    name = getattr(name, "id", None)
                    if name not in constants:
                        continue
                    text = constants[name]
                else:
                    continue
            else:
                continue
            if text.startswith(FAILURE_OPENINGS):
                literals.add(text)
    return sorted(literals), found


def _failure_literals() -> list[str]:
    literals, found = _scan_failure_literals(TOOLS_SOURCE, FAILURE_FUNCTIONS)
    # A rename or a move upstream has to fail here: without this the scan finds nothing to check and
    # every assertion below it passes for the wrong reason.
    assert found == FAILURE_FUNCTIONS, FAILURE_FUNCTIONS - found
    return literals


@pytest.mark.parametrize("message", FAILURES)
def test_a_failure_string_becomes_that_urls_error(client, monkeypatch, message):
    _engine(monkeypatch, {"https://example.com/a": message})
    response = client.post(
        ENDPOINT, json = {"urls": ["https://example.com/a", "https://example.com/b"]}
    )
    assert response.status_code == 200
    first, second = response.json()["results"]
    assert (first["url"], first["text"], first["error"]) == (
        "https://example.com/a",
        "",
        message,
    )
    # The other URL of the call still comes back.
    assert second["error"] is None and second["text"]


@pytest.mark.parametrize("prefix", web_mod._FAILURE_PREFIXES)
def test_every_failure_prefix_is_a_literal_in_the_engine(prefix):
    # A reword upstream has to fail a test here, not silently unmask a failure as page text.
    assert prefix in TOOLS_SOURCE, prefix


def test_every_engine_failure_literal_is_classified():
    # Positive control: the scan does see the strings, or the loop below proves nothing.
    literals = _failure_literals()
    assert "Failed to resolve host" in TOOLS_SOURCE
    assert "(compressed content exceeds the " in literals
    assert "Failed to decode the compressed response: " in literals
    for literal in literals:
        assert web_mod._failed(literal) is not None, literal


def test_the_resolver_failure_is_scanned_through_its_constant():
    # The resolver reports with f"{_RESOLVE_FAILED}: ...", so the scan has to resolve the name
    # rather than skip the f-string: a dead hostname is the commonest real per-URL failure there is.
    assert isinstance(engine._RESOLVE_FAILED, str)
    assert engine._RESOLVE_FAILED in _failure_literals()
    assert (
        web_mod._failed(f"{engine._RESOLVE_FAILED}: [Errno -2] Name or service not known") is not None
    )


def test_every_url_access_refusal_is_classified():
    # check_url_access is on the fetch path and its reason is returned verbatim as the page's error,
    # so a reword there must fail here rather than hand a refusal back as page text.
    literals, found = _scan_failure_literals(POLICY_SOURCE, POLICY_FUNCTIONS)
    assert found == POLICY_FUNCTIONS
    # All six of its refusals, counted so a reworded opening cannot make this scan vacuous.
    assert len(literals) == 6, literals
    for literal in literals:
        assert literal.startswith("Blocked: "), literal
        assert web_mod._failed(literal) is not None, literal
    assert web_mod._failed("Blocked: website access policy disallows example.com.") is not None


def test_a_pdf_that_parsed_is_text_not_an_error(client, monkeypatch):
    pdf = "## Page 1\n\nHello world.\n\n... (PDF extraction text limited to 16,000 characters)"
    _engine(monkeypatch, {"https://example.com/a.pdf": pdf})
    (result,) = client.post(
        ENDPOINT, json = {"urls": ["https://example.com/a.pdf"]}
    ).json()["results"]
    assert (result["text"], result["error"]) == (pdf, None)


def test_an_engine_exception_is_that_urls_error_not_a_500(client, monkeypatch):
    def fetch(url, max_chars = None, timeout = None):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(web_mod, "_fetch_page_text", fetch)
    response = client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]})
    assert response.status_code == 200
    (result,) = response.json()["results"]
    assert result["text"] == ""
    assert result["error"] == "Failed to fetch URL: RuntimeError."


def test_a_call_that_outruns_its_deadline_reports_the_slow_url(client, monkeypatch):
    monkeypatch.setattr(web_mod, "EXTRACT_DEADLINE_S", 0.05)
    _engine(monkeypatch, delays = {"https://example.com/slow": 0.3})
    response = client.post(
        ENDPOINT,
        json = {"urls": ["https://example.com/slow", "https://example.com/fast"]},
    )
    assert response.status_code == 200
    slow, fast = response.json()["results"]
    assert slow == {
        "url": "https://example.com/slow",
        "text": "",
        "error": "Failed to fetch URL: timed out.",
    }
    # A page that finished inside the deadline still comes back.
    assert fast["error"] is None and fast["text"]


def test_the_fetch_runs_on_the_dedicated_pool(client, monkeypatch):
    seen = _engine(monkeypatch)
    assert client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]}).status_code == 200
    assert seen[0]["thread"].startswith("web-extract")
    assert seen[0]["thread_id"] != threading.get_ident()


def test_a_full_in_flight_cap_is_a_503(client, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking = False)
    monkeypatch.setattr(web_mod, "_FETCH_SLOTS", slots)
    seen = _engine(monkeypatch)
    response = client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]})
    assert response.status_code == 503
    assert "Too many web fetches in flight" in response.json()["detail"]
    assert seen == []
    # The refusal took nothing, and the slot the test holds is still worth something afterwards.
    assert _slots_left(slots) == 0
    slots.release()
    assert client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]}).status_code == 200
    assert _slots_left(slots) == 1


def test_a_call_takes_all_its_slots_or_none(client, monkeypatch):
    # One free slot cannot serve two URLs; a half-admitted call would report an error the caller
    # could not tell from a real one. The slot it did take must come back, or a single 503 shrinks
    # the installation's capacity for good.
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(web_mod, "_FETCH_SLOTS", slots)
    seen = _engine(monkeypatch)
    response = client.post(ENDPOINT, json = {"urls": _urls(2)})
    assert response.status_code == 503
    assert seen == []
    assert _slots_left(slots) == 1
    # A call that fits is admitted afterwards, which is the same invariant through the public door.
    assert client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]}).status_code == 200
    assert _slots_left(slots) == 1


def test_a_successful_call_gives_its_slots_back(client, monkeypatch):
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(web_mod, "_FETCH_SLOTS", slots)
    _engine(monkeypatch)
    # The same URL twice, so a per-URL keyed release cannot pass this by accident.
    urls = ["https://example.com/a", "https://example.com/a"]
    assert client.post(ENDPOINT, json = {"urls": urls}).status_code == 200
    assert _slots_left(slots) == 2
    assert client.post(ENDPOINT, json = {"urls": urls}).status_code == 200
    assert _slots_left(slots) == 2


def test_an_engine_exception_gives_its_slot_back(client, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(web_mod, "_FETCH_SLOTS", slots)

    def fetch(url, max_chars = None, timeout = None):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(web_mod, "_fetch_page_text", fetch)
    response = client.post(ENDPOINT, json = {"urls": _urls(1)})
    assert response.status_code == 200
    assert response.json()["results"][0]["error"] == "Failed to fetch URL: RuntimeError."
    assert _slots_left(slots) == 1
    _engine(monkeypatch)
    assert client.post(ENDPOINT, json = {"urls": _urls(1)}).status_code == 200
    assert _slots_left(slots) == 1


def test_an_expired_deadline_gives_its_slot_back_when_the_worker_ends(client, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(web_mod, "_FETCH_SLOTS", slots)
    monkeypatch.setattr(web_mod, "EXTRACT_DEADLINE_S", 0.05)
    _engine(monkeypatch, delays = {"https://example.com/slow": 0.3})
    response = client.post(ENDPOINT, json = {"urls": ["https://example.com/slow"]})
    assert response.status_code == 200
    assert response.json()["results"][0]["error"] == "Failed to fetch URL: timed out."
    # Held until the abandoned worker ends: the socket is still open, so the slot has to stay taken.
    assert _slots_left(slots) == 0
    deadline = time.monotonic() + 5
    while _slots_left(slots) == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _slots_left(slots) == 1
    _engine(monkeypatch)
    assert client.post(ENDPOINT, json = {"urls": ["https://example.com/a"]}).status_code == 200
    assert _slots_left(slots) == 1
