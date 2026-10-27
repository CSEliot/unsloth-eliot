# Code review: `web_extract` (branch `hermes-web-tools`, uncommitted)

Verdict: **APPROVE WITH FIXES**. No BLOCKERs. The concurrency design is sound and I could not
leak or over-release a slot on any path I could build. The one MAJOR is error classification: the
prefix list misses `Failed to resolve host`, which is the commonest real per-URL failure, so a dead
DNS name is handed to the agent as page text with `error = null`.

Scope reviewed: `git diff` of the eight tracked files plus the four new files read in full,
`studio/backend/core/inference/tools.py` (unchanged) for the exact failure literals, the tests run
in the repo venv, and three throwaway probes against the route.

## Findings

| # | Severity | Path:line | Problem | Fix |
|---|----------|-----------|---------|-----|
| 1 | MAJOR | `studio/backend/routes/web.py:76-83` | `_FAILURE_PREFIXES` misses `"Failed to resolve host"` (`core/inference/tools.py:126`, returned at `:16524`, `:16527`, `:16709`). A typo'd or dead domain comes back as `{"text": "Failed to resolve host: no addresses for 'x'", "error": null}`, so the MCP result reads as a successful page whose content is an error string. This is the most likely per-URL failure in practice and it silently inverts the tool's documented contract. The same gap swallows `"(compressed content exceeds the N byte limit)"` and `"Failed to decode the compressed response: ..."` (`tools.py:16998-16999`), both reachable through `_fetch_page_text` via `_read_capped_body`. Probe D below confirms all three land in `text`. | Add `"Failed to resolve host"` to `_FAILURE_PREFIXES`; add `"(compressed content exceeds "` and `"Failed to decode the compressed response: "`. Add the resolver string to `FAILURES` in `tests/test_web_extract_route.py:149-158` so an upstream reword fails a test. Optionally assert in that test that every `return` literal beginning `"Failed to "`/`"Blocked: "`/`"("` in `tools.py` is covered, which is what the builder's report claimed and this list does not do. |
| 2 | MINOR | `studio/backend/studio_mcp/tools/web.py:42` | The description states the split as an exact figure ("1 URL gets 16000, 2 get 10000 each, 5 get 4000 each"), but the engine fits the text to the loaded model's window: `_truncate_page_text` calls `_dense_char_limit` (`tools.py:18033`), which reads `_loaded_context_tokens()` when the ContextVar is unset, as it is on a pool thread. Probe H: with a 4096-token window, a 16000-character request returns 5770 characters. So the same call returns different amounts depending on whether some unrelated model happens to be loaded, which is surprising for a tool that needs no model. `agents/task0_review.md` item 7 already flagged this. | Say "up to": "1 URL gets up to 16000 characters". If exactness matters, the route would have to opt out of the window fit, which means touching `core/inference/tools.py` and is out of scope for this change. |
| 3 | MINOR | `studio/backend/routes/web.py:62,69` | `EXTRACT_IN_FLIGHT` is read from the environment with no floor, so `UNSLOTH_STUDIO_WEB_EXTRACT_IN_FLIGHT=3` (a plausible tweak for a rate-limited site) makes every 4- and 5-URL call a permanent 503, because one call takes one slot per URL and takes all of them or none. Nothing in the code or the comment says the cap must exceed `MAX_EXTRACT_URLS`. | `EXTRACT_IN_FLIGHT = max(_env_int(...), MAX_EXTRACT_URLS)`, or refuse to start with a cap below it. |
| 4 | MINOR | `studio/backend/routes/web.py:54-55` | The comment says "Raising this above the engine's cap changes nothing: the engine truncates at its own limit first." That is not true: `_MAX_PAGE_CHARS` only applies when `max_chars is None` (`_page_char_budget`); `_truncate_page_text` honours whatever cap it is given. Today the route's own `min(...)` keeps the 16000 default, so behaviour is right, but the comment will mislead the next person who raises the knob. | Reword to "the engine uses whatever cap it is given, so the route's own `min(..., MAX_EXTRACT_PAGE_CHARS)` is what holds the 16000 page cap", or clamp `MAX_EXTRACT_PAGE_CHARS` to `_MAX_PAGE_CHARS` at definition. |
| 5 | MINOR | `studio/backend/tests/test_web_extract_route.py:235-242` | Slot release is never asserted. `test_a_call_takes_all_its_slots_or_none` would still pass with the release loop at `routes/web.py:158-159` deleted, and nothing checks that a completed, failed or timed-out worker gives its slot back. Probe A/B/C/G show the current code is correct; the tests that pin the module's core invariant are missing. | In the 503 test, keep a handle on the patched `BoundedSemaphore` and assert its value is back to 1 afterwards (or follow with a request that must answer 200). Add one test that runs the same URL twice under `BoundedSemaphore(1)`, and one that lets the deadline expire and then asserts a later call is admitted once the abandoned worker has finished. |
| 6 | NIT | `studio/backend/tests/test_mcp_no_host_paths.py:156-163` | Adding `url` and `text` to the ignore set stops that test from checking either field: `poison()` suffixes every string, so both are now unexamined. Not a real hole for `url` (`routes/web.py:181` echoes the request string, and `test_mcp_tools_web.py:31-34` pins the exact URLs), and `text` is third-party content that scrubbing would corrupt, matching the existing `chat.text` precedent. But the field-level ignore is broader than the reason for it. | Keep `text` ignored. For `url`, either drop it from the ignore set and serve the case's URLs unpoisoned (they are real https URLs, no sentinel paths), or state in the comment that `test_mcp_tools_web.py` carries the `url` assertion. |
| 7 | NIT | `studio/backend/routes/web.py:7,42,71,122,152` | Six lines run 101-102 characters, over the repo's 100. `E501` is not selected (`pyproject.toml:1770-1784`), and neighbouring files carry far longer comment and docstring lines, so this is cosmetic only. | Rewrap those five comment and docstring lines. |
| 8 | NIT | `studio/backend/routes/web.py:90-93` | The route bounds each URL string (`max_length = 8192`) and `max_chars` only `ge = 1`, while the MCP tool bounds `max_chars` at 16000 and does not bound URL length. Consequences are benign (route clamps, over-long URL answers 422 which `route_json` turns into a `ToolError`), but the two schemas disagree and the tool description does not say URLs are length-bounded. | Either document the 8192 URL bound in the tool description, or drop it from the route in favour of the engine's own handling. Not blocking. |
| 9 | NIT | `studio/backend/routes/web.py:69` | One installation-wide cap and no per-account fairness: a single caller can hold all 24 slots for up to 60 s and 503 every other caller. Already listed as an accepted v1 gap in `agents/task0_build.md`, restated here so it is a decision rather than an oversight. | Leave for v1; a per-account counter is the cheap next step. |

## What I verified and found correct

- **Slot accounting, every path.** Acquire loop is non-blocking and has no `await`, so it cannot be
  cancelled midway. The 503 path releases every held slot; the submit-failure path releases
  `held - len(tasks)`; each worker releases its own slot in `finally`, and the semaphore object is
  passed as an argument, so a test that swaps the global cannot make a worker over-release the real
  one. Probes: A (two sequential calls under `BoundedSemaphore(1)`, value stays 1), B (engine raises,
  value back to 1), C (deadline), G (task cancelled during `asyncio.wait`, abandoned worker still
  releases), plus partial-acquire probes with 1 and 2 slots that show the refusal leaves the
  semaphore at its starting value and that no worker raised a `ValueError` from a double release.
- **Deadline behaviour.** Probe C: a 0.05 s deadline against a 0.6 s fetch returns HTTP 200 in
  0.054 s with `Failed to fetch URL: timed out.` for that URL only; the slot is still held right
  after the response and released when the worker ends (0.8 s later), which is the intended "held
  until the thread ends". `_drain` is registered on the pending futures, so a late exception is not
  reported as unretrieved.
- **Budget math.** `_page_budget` gives 16000 / 10000 / 6666 / 4000 for 1 / 2 / 3 / 5 URLs, honours
  a smaller caller cap (500 stays 500), clamps a larger one (100000 becomes 16000 at one URL, 4000
  at five), and never returns 0. A `MAX_EXTRACT_TOTAL_CHARS` smaller than the URL count degrades to 1
  character per page rather than 0.
- **Input order.** `zip(urls, tasks)` and the per-URL dicts keep request order; the route test makes
  the first URL the slowest and still expects input order.
- **Auth.** `Depends(get_current_subject)` present; the route tests exercise the real dependency
  (401 keyless, 403 on two `Authorization` headers, 401 on a bad key) and assert the engine is never
  called on refusal. `/api/web` is covered by the blanket `/api/` body cap in `main.py:1560-1566`;
  no other registration list needs the new prefix.
- **SSRF.** The route passes the caller's URL straight to `_fetch_page_text`, which still runs
  `check_url_access` (scheme, credentials, whitespace, policy) and the pinning resolver; nothing in
  `routes/web.py` or the MCP tool reaches `urllib` or resolves a host itself. `file://` and local
  paths answer `Blocked: only http/https URLs are allowed.`, classified as an error. The route passes
  no `website_policy`; that policy is per-chat (`routes/inference.py`), so there is none to pass, and
  `routes/browser.py` takes the same position for its fetches.
- **Host paths.** `error` is `RouteText` and is scrubbed; `url` is echoed from the request, never from
  the engine's `meta["url"]`, so no Studio path can enter it; `text` is internet content. No failure
  string in `tools.py` that could carry a filesystem path lands in `text` today, but finding 1 is the
  reason that stays true only by luck: fixing the prefix list also pushes those strings through the
  scrubbed `error` channel.
- **MCP schema and annotations.** Dumped from the live registry: `urls` has `minItems 1`,
  `maxItems 5`; `max_chars` is `1..16000` with `default null`; `read_only_hint True`,
  `open_world_hint True`, `destructive_hint None`; output schema is `pages[]` of
  `{url, text, error}` with `additionalProperties false`. Description covers no-JS, Markdown, PDF,
  per-URL errors, the requested-URL-not-final-URL point and the split.
- **ToolError mapping.** `route_json` turns a non-2xx into `ToolError` carrying the route's detail
  and the status; the 400 test asserts both.
- **Conventions.** SPDX header byte-identical to `routes/browser.py`; spaced kwargs throughout
  (`ruff format` is not the repo's enforcement, `git` shows it would reformat untouched files too);
  `ruff check` clean on the new files; no emdashes; `main.py` footprint is exactly two lines next to
  the browser router, same import shape, so the merge-conflict surface in that hot file is minimal;
  no drive-by edits in any hunk.

## Test results

Command, from `studio/backend` with `../../.venv/bin/python`:

```
pytest tests/test_web_extract_route.py tests/test_mcp_tools_web.py tests/test_mcp_server.py \
       tests/test_mcp_tool_contract.py tests/test_mcp_no_host_paths.py \
       -q --tb=short -n 4 --dist loadgroup --timeout=330
```

Result: **185 passed, 3 warnings in 12.70 s**. The warnings are the pre-existing FastMCP
deprecations for `ToolAnnotations.openWorldHint`/`readOnlyHint`/`destructiveHint` reads in
`tests/test_mcp_server.py:57-61`; the new line 57 uses the same deprecated alias as its neighbours,
so it neither adds nor removes a warning. No test touched the network.

Gaps in the suite, beyond finding 5: no case for `max_chars` above 16000 (accepted and clamped, not
refused), no case for the resolver failure (finding 1), no case for a route row that omits `url` or
for a short `results` list (the `ToolError` at `studio_mcp/tools/web.py:51-52` is untested), and
`test_a_pdf_that_parsed_is_text_not_an_error` would still pass if the `(PDF ` prefix were dropped
from `_FAILURE_PREFIXES` because its sample text starts with `## Page 1`.

## Probes (throwaway, under the scratch dir, no repo file touched)

- A: success path, `BoundedSemaphore(1)`, two calls, semaphore value 1 -> 1. B: engine raises
  `RuntimeError`, value back to 1, error string `Failed to fetch URL: RuntimeError.`. C: deadline
  0.05 s vs 0.6 s fetch, 200 in 0.054 s, slot 0 right after the response, 1 after the worker ends.
  D: `_failed()` over 13 strings, see finding 1. E: `_page_budget` matrix for 1/2/3/5 URLs and caps
  None/500/16000/100000. F: `{"urls": ["", ...]}` answers 422 before any fetch. G: cancel during
  `asyncio.wait`, slot held and then released, no unretrieved-exception log. H: 4096-token window,
  `_truncate_page_text("a" * 40000, 16000)` returns 5770 characters; 32768-token window returns
  16036. I: partial-acquire refusals with 1 and 2 slots, semaphore restored, no worker exception.
