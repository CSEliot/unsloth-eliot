# Task 0 build: `web_extract` MCP tool, first pass

Branch `hermes-web-tools` (from main 83c1c6256). Nothing committed; everything is in the working tree.
The route and tool exist and are tested offline. No server was started, no request went to
localhost:8888, `~/.unsloth/studio` was not read or written, and no credentials file was opened.

## Files changed

- `studio/backend/routes/web.py` (new): `POST /api/web/extract`, the per-call character budget, the
  failure-string classification, a dedicated `web-extract` pool and one module-level in-flight cap.
- `studio/backend/main.py`: two lines, `import routes.web as _web_routes` and
  `app.include_router(_web_routes.router, prefix = "/api/web", tags = ["web"])` right after the browser
  router.
- `studio/backend/studio_mcp/tools/web.py` (new): the `web_extract` tool, its description, and a
  `TOOLS` tuple with `readOnlyHint = True, openWorldHint = True`.
- `studio/backend/studio_mcp/outputs.py`: `WebPage(url, text, error)` and `WebExtractResult(pages)`.
- `studio/backend/studio_mcp/forward.py`: `"/api/web/"` added to `ALLOWED_PREFIXES`.
- `studio/backend/mcp_server.py`: `web` in the tool-module import block and in the module tuple.
- `studio/backend/tests/test_web_extract_route.py` (new): auth, validation, budget split, error
  mapping, PDF success, own-thread, deadline, 503.
- `studio/backend/tests/test_mcp_tools_web.py` (new): forward shape, order, per-page error, refused
  inputs, route failure.
- `studio/backend/tests/test_mcp_server.py`: `web_extract` in `READ_ONLY`, new `OPEN_WORLD` set, line
  52 now `hints.openWorldHint is (name in OPEN_WORLD)`.
- `studio/backend/tests/test_mcp_tool_contract.py`: three refused-input rows for `web_extract`.
- `studio/backend/tests/test_mcp_no_host_paths.py`: a `web_extract` case plus its two verbatim output
  keys (see deviations).
- `studio/MCP.md`: one tools-table row and a short paragraph under "Unsloth Studio's own MCP server".

`core/inference/tools.py` and `routes/browser.py` are untouched.

## Design decisions and deviations

- **Engine reused unchanged.** `_fetch_page_text` is imported into `routes/web.py` and called with
  `max_chars` and `timeout` only; the tests monkeypatch `web_mod._fetch_page_text`, so no network is
  used anywhere.
- **Env-overridable constants.** `routes/web.py` defines its own `_env_int` (the same pattern as
  `core/inference/tools.py:190`) rather than importing that private helper, so every limit is a named
  module constant with an `UNSLOTH_STUDIO_WEB_EXTRACT_*` override: `MAX_URLS`, `URL_CHARS`,
  `TOTAL_CHARS` (20000), `PAGE_CHARS` (defaults to the engine's `_MAX_PAGE_CHARS`, imported so the two
  cannot drift), `POOL_SIZE` (8), `IN_FLIGHT` (24), `DEADLINE_S` (60), `URL_TIMEOUT_S` (30).
- **Threading.** One module-level `ThreadPoolExecutor(thread_name_prefix = "web-extract")`, plus one
  module-level `threading.BoundedSemaphore`. The semaphore is taken once per URL in the handler
  (non-blocking) and released in the worker's `finally`, so it bounds fetches queued plus running, like
  `routes/browser.py:64-71`. All of a call's slots are taken or none: a partly admitted call would
  report errors the caller could not tell from real ones. `asyncio.to_thread` and the default executor
  are not used.
- **Deadline.** `asyncio.wait(tasks, timeout = EXTRACT_DEADLINE_S)`; URLs still pending get
  `"Failed to fetch URL: timed out."` and are left to finish on their own (their `finally` still
  releases the slot), mirroring browser.py's drain-without-cancel approach.
- **Budget.** `min(caller max_chars or page cap, page cap, total // len(urls))`, floored at 1. So 1 URL
  gets 16000, 2 get 10000, 5 get 4000, and a caller cap below the share wins. `max_chars` in the
  request is validated `ge = 1` only: anything above the page cap is clamped rather than refused.
- **Error mapping** is by prefix on the engine's returned string, from the exact literals in
  `tools.py`: `Failed to fetch URL: `, `Blocked: `, `(non-text content`, `(binary content`, `(PDF `,
  `(page returned no readable text`. The bot-check message starts with `Failed to fetch URL: `, so it
  needs no separate prefix. A successful PDF comes back as text or with a `... (PDF extraction ...)`
  suffix, so the `(PDF ` prefix cannot match it; a test pins that. Each prefix has its own route test
  parametrization so an upstream reword fails a test.
- **MCP annotations.** New `READ_ONLY_WEB` (`readOnlyHint = True, openWorldHint = True`) instead of
  reusing `READ_ONLY`. `INSTRUCTIONS` in `mcp_server.py` was left alone (the 240-char headroom and the
  `<= 1900` assertion in test_mcp_server.py:142 make it not worth the risk); the tool description and
  `studio/MCP.md` carry the JavaScript, Markdown, url-provenance, per-URL-error and budget facts.
- **test_mcp_no_host_paths.py was not in the plan.** It asserts every registered tool has a poisoned
  host-path case (`set(CASES) == names`), so adding a tool fails it. It now has a `web_extract` case
  (one page plus one per-URL error). Its `MODEL_TEXT` ignore set also gained `web_extract: {"url",
  "text"}`: `error` is `RouteText` and is scrubbed, but a page's `text` is internet content and the
  `url` is the caller's own string, so redacting paths inside them would mangle real page content. The
  comment above `MODEL_TEXT` was widened to say "text that came from outside Unsloth Studio".
- **`WebPage.text` is a plain `str`, `WebPage.error` is `RouteText`.** Deliberate: the error text comes
  from Studio's route and is scrubbed; the page text is what the caller asked for.
- **Auth reality.** In this FastAPI version a missing credential answers **401** "Not authenticated",
  not 403. 403 is reachable only with two `Authorization` headers, which the auth dependency refuses.
  The route test covers both.

## Test environment

`.venv` at the repo root, Python 3.13.15 (CI's full-run interpreter; see
`.github/workflows/studio-backend-ci.yml:146`), created with uv and installed exactly as the CI "Install
backend test dependencies (CPU only)" step (studio.txt, the extras list, CPU torch, transformers, the
peft pin from extras-no-deps.txt, torchao). One note: uv resolved `torch 2.10.0+cu128` rather than the
CPU-only wheel, because uv searches `--extra-index-url` with the same priority as `--index-url`, unlike
pip. It is a CPU run (`torch.cuda.is_available()` is False) and every test below passed on it; the venv
is ~7.9 GB. ruff 0.17.2 and the pytest plugins came from the same step.

The install took about 20 minutes, so the touched existing test files could not be run on the untouched
tree first. That is why the pre-existing-failure question is answered by inspection instead: the only
failure seen in the whole suite was `tests/test_mcp_no_host_paths.py::test_every_registered_tool_has_a_host_path_case`,
which is caused by the new tool (its message names `web_extract`), and it was fixed by adding the case
described above. `tests/test_browser_routes.py`, which this change does not modify, passes.

## Exact commands and results

From `studio/backend`, with `../../.venv/bin/python`:

1. `python -m pytest tests/test_mcp_server.py tests/test_mcp_tools_web.py tests/test_mcp_tool_contract.py tests/test_web_extract_route.py tests/test_browser_routes.py -q --tb=short -n 4 --dist loadgroup --timeout=330`
   -> **288 passed**, 3 warnings (FastMCP deprecation warnings from the existing
   `hints.readOnlyHint`-style reads; fastmcp 4.1.4 in this venv warns about them, the repo's floor does
   not).
2. `python -m pytest tests/test_mcp_access_route.py ... tests/test_mcp_preregistered_oauth.py -q --tb=short -n 4 --dist loadgroup --timeout=330`
   (23 files) -> first run **1 failed, 775 passed**: the `test_mcp_no_host_paths` case above. After the
   fix, `python -m pytest tests/test_mcp_no_host_paths.py -q ... -n 4` -> **28 passed**.
3. `python -m pytest tests/test_mcp_progress.py ... tests/test_mcp_tool_read_off_event_loop.py -q --tb=short -n 4 --dist loadgroup --timeout=330`
   (15 files) -> **484 passed, 5 skipped**.
4. `python -m pytest tests/test_mcp_tools_audio.py ... tests/test_mcp_video_queue.py -q --tb=short -n 4 --dist loadgroup --timeout=330`
   (27 files, all the remaining `tests/test_mcp_*.py`) -> **711 passed**.
5. `python -m pytest -q --tb=short --timeout=330 tests/test_web_fetch_extraction.py` (serial, as CI runs
   it) -> **1457 passed**.

No test touches the network: the route tests replace the engine, and the MCP tests use the fake Studio
from `tests/mcp_harness.py`.

## Lint

- `.venv/bin/ruff check --select E9,F63,F7,F82` on all eleven changed Python files: **All checks
  passed.**
- `python scripts/enforce_kwargs_spacing.py` (full pass) on the four new files and `--residual` on the
  seven edited ones: exit 0, no adjustments.
- Stock `ruff format` was not run (the repo pins a ruff fork).

## Open issues

- The 60 s deadline and the 20 s/4000-char shares are untuned guesses; they are all env variables now,
  so tuning needs no code change.
- No per-account rate limit or politeness delay (the review's gap 6). One installation-wide cap only.
- No final URL or title in the result (v1 scope, stated in the tool description).
- The live test against port 8889 (a real fetch through the running app) is a later step and was not
  done here.
- The `INSTRUCTIONS` blurb for `web_extract` was skipped to stay under the 1900-character assertion.

## Fix round 1

Code-review (`agents/task0_code_review.md`) and live-test (`agents/task0_livetest.md`) fixes. No
commit. Only the files named below changed; `core/inference/tools.py` and `routes/browser.py` were
not touched.

1. **MAJOR, failure classification.** `studio/backend/routes/web.py:101-116` adds
   `"Failed to resolve host"` (tools.py `_RESOLVE_FAILED`, returned at :16524, :16527, :16709),
   `"Failed to decode the compressed response: "` and `"(compressed content exceeds "`
   (`_read_capped_body`, tools.py:16998-16999), plus `"(content exceeds "` from
   `_fetch_url_raw`'s `raw_bytes_max` path (tools.py:17306), which this route cannot reach today but
   would otherwise regress into page text if a byte cap were added. `"Blocked: "` is unchanged.
   Every other failure string `_fetch_page_text` can return (followed through
   `check_url_access`, `_redirect_hop`, `_fetch_budget_exceeded`, `_truncate_page_text`) already
   matched an existing prefix; the AST test below now proves that instead of assuming it.
   `tests/test_web_extract_route.py:259-276` adds the five new strings to `FAILURES`, and two new
   tests pin the list to the engine: `test_every_failure_prefix_is_a_literal_in_the_engine` (:341)
   asserts each prefix appears in tools.py source, and
   `test_every_engine_failure_literal_is_classified` (:346) walks tools.py's AST over the eight
   functions on the fetch path, collects every literal (or f-string leading constant) opening with
   `Failed to `, `Blocked: ` or `(`, and asserts `_failed()` classifies it: 18 literals today. An
   upstream reword now fails a test instead of unmasking an error as page text.

2. **Per-page ceiling overshoot.** `routes/web.py:82-88` adds `_TRUNCATION_NOTICE` and
   `TRUNCATION_NOTICE_RESERVE`, computed as the format's length with a 20-digit total (51 chars,
   longer than the notice any fetched page can produce). `_page_budget` (:139-146) subtracts it from
   the share and floors at 1. The engine now receives 15949 / 9949 / 3949 for 1 / 2 / 5 URLs, so the
   text a caller gets back is <= 16000 / 10000 / 4000 and the whole call <= 20000. Tests:
   `test_no_page_arrives_over_its_share` (:223) drives a fake engine that appends the same notice and
   asserts `len(text) <= share` and `sum <= 20000` for 1, 2 and 5 URLs;
   `test_the_real_engine_notice_still_fits_the_share` (:234) calls the real
   `_truncate_page_text` with no network and asserts the same ceiling end to end. Existing cap
   expectations were updated to the new engine caps, not deleted.

3. **Description says "up to".** `studio_mcp/tools/web.py:44-52`: the split is now described as
   ceilings ("1 URL gets up to 16000, ..."), and the description says a page shorter than its share
   comes back whole and that a loaded model with a small context window shrinks every page further
   via `_dense_char_limit`. `studio/MCP.md:79-86` says "up to about 20,000 ... less when a model with
   a small context window is loaded" for the same reason.

4. **Env floors.** `routes/web.py:33-44` gives `_env_int` a `minimum` argument and clamps there;
   `EXTRACT_IN_FLIGHT` (:74-76) passes `minimum = MAX_EXTRACT_URLS`, so a 2-slot override can no
   longer make every 5-URL call a permanent 503, and every other knob floors at 1.
   `test_env_tunables_are_clamped_to_a_usable_minimum` (:142) sets all eight overrides low (2
   in-flight, 0 pool, -100 total, 0 page, 0 deadline, -1 timeout, 0 url chars), reloads the module,
   asserts in-flight == MAX_EXTRACT_URLS and every constant >= 1, then reloads with the env cleared
   to leave the module as it was found.

5. **Wrong comment near `MAX_EXTRACT_PAGE_CHARS`.** `routes/web.py:59-62` now says the engine
   truncates to whatever cap it is handed and only uses its own constant when `max_chars is None`, so
   `_page_budget`'s `min()` holds the 16000 default and raising the knob really does raise the page
   size.

6. **Slot release asserted.** `tests/test_web_extract_route.py`: `test_a_call_takes_all_its_slots_or_none`
   (:418) now asserts the semaphore is back to 1 after the 503 and that a following call is admitted;
   `test_a_full_in_flight_cap_is_a_503` (:402) asserts the refusal left the semaphore untouched and
   that the caller's own slot still works; `test_a_successful_call_gives_its_slots_back` (:434) runs
   the same URL twice under `BoundedSemaphore(2)` and asserts 2 free slots before and after a second
   call; `test_an_engine_exception_gives_its_slot_back` (:446); and
   `test_an_expired_deadline_gives_its_slot_back_when_the_worker_ends` (:463) asserts the slot is
   still held right after the timed-out response (value 0) and back to 1 once the worker ends, then
   admits a later call. Verified by temporarily commenting out the release loop at
   `routes/web.py:193-194`: `test_a_call_takes_all_its_slots_or_none` failed (`assert 0 == 1`) and
   the loop was restored byte for byte; the suite was re-run green afterwards.

7. **`test_mcp_no_host_paths` narrowed.**
   `studio/backend/tests/test_mcp_no_host_paths.py:60-84` replaces the static web payload with
   `_web_answer`, a fake-Studio handler that answers with the https URLs it was asked for, left
   unpoisoned (text and error are still poisoned). `MODEL_TEXT["web_extract"]` (:175-184) now ignores
   only `text`, so the tool's echo of the caller's URL is checked again.

8. **MCP URL length bound.** `studio_mcp/tools/web.py:19-35` adds `MAX_EXTRACT_URL_CHARS = 8192` and
   bounds each list item with `Annotated[str, Field(min_length = 1, max_length = ...)]`, so the
   listed schema carries `maxLength` on `urls/items`. `tests/test_mcp_server.py:107-109` pins
   `urls/items/maxLength = 8192` and `urls/maxItems = 5`, and
   `tests/test_mcp_tool_contract.py:69` adds a 9,000-character URL to `REFUSED`, proving the tool
   refuses it before any route is called. The route's `max_chars` stays clamp-only (`ge = 1`).

9. **Installation-wide cap documented.** `routes/web.py:71-73` states that the in-flight cap is
   installation-wide with no per-account share, that one caller can hold every slot for up to
   `EXTRACT_DEADLINE_S`, and that this is a known first-pass limit with a per-account counter as the
   next step. `studio/MCP.md:87-88` says the same to API users.

Lint: `scripts/enforce_kwargs_spacing.py` on all six changed Python files -> exit 0, no adjustments.
`.venv/bin/ruff check --select E9,F63,F7,F82` on the same six -> All checks passed. Stock `ruff
format` not run. New lines are <= 100 characters (the pre-existing 101-102 character lines that
were not being edited were left alone).

Tests, repo venv, from `studio/backend` (final state, all after the last edit):

```
pytest tests/test_web_extract_route.py tests/test_mcp_tools_web.py tests/test_mcp_server.py \
       tests/test_mcp_tool_contract.py tests/test_mcp_no_host_paths.py tests/test_browser_routes.py \
       -q --tb=short -n 4 --dist loadgroup --timeout=330
-> 345 passed, 3 warnings in 15.82 s

pytest tests/test_mcp_*.py -q --tb=short -n 4 --dist loadgroup --timeout=330
-> 2100 passed, 5 skipped, 18 warnings in 89.42 s

pytest -q --tb=short --timeout=330 tests/test_web_fetch_extraction.py   (serial)
-> 1457 passed in 25.58 s
```

The 3 warnings in the first run are the pre-existing FastMCP `ToolAnnotations.*Hint` deprecations in
`test_mcp_server.py:57-61`; the skipped tests in the MCP run are the pre-existing platform skips.
Nothing touched the network: the route tests replace the engine (except the real
`_truncate_page_text`, which is pure string work), and the MCP tests use the fake Studio.

Not done / limits: the `(content exceeds ` prefix is unreachable through this route today (it needs
`raw_bytes_max`), so it is defence only and its `FAILURES` row cannot be produced by the real engine.
The in-flight cap is still installation-wide: fix 9 documents it, nothing implements per-account
fairness. No live test was re-run against a server (out of scope for this round, and no server was
started).

## Fix round 2

Applied the review-2 findings (F1-F8) to the same uncommitted work. No commit, no server, no
network. F9 and F10 were accepted as-is, per the review.

1. **F1, caller `max_chars` under 36 came back longer than asked.** `routes/web.py:62` adds
   `MIN_EXTRACT_PAGE_CHARS = 64`; `routes/web.py:140` makes it the `ExtractRequest` bound
   (`ge = 64` instead of `ge = 1`), and `studio_mcp/tools/web.py:27,45` (`MIN_EXTRACT_CHARS`) does
   the same for the MCP `Field`, so 0..63 is a 422 and a tool refusal before the route. The
   truncation-notice reserve and the new minimum now sit above the env knobs, and
   `routes/web.py:83` floors `UNSLOTH_STUDIO_WEB_EXTRACT_PAGE_CHARS` at the same minimum, because a
   page knob under it put the default path back into the overrun. `_page_budget`'s docstring
   (`routes/web.py:153-160`) says the floor now only guards a mis-set deployment. Probes with the
   real `_truncate_page_text`: cap 64 -> budget 13, text 49; 65 -> 14, 50; 100 -> 49, 85; 1200 ->
   1149, 1185; 16000 -> 15949, 15985. All within the cap. Tests:
   `tests/test_web_extract_route.py:124` (422 at 63), `:214`
   `test_an_accepted_cap_is_never_exceeded_at_the_minimum` (real engine, 64/65/100/1200/16000),
   `tests/test_mcp_tool_contract.py:73` (63 refused), `tests/test_mcp_server.py:112` (schema
   `minimum = 64`), `tests/test_mcp_tools_web.py:22` (the two constants agree). Tool description
   (`studio_mcp/tools/web.py:55-58`) and the `max_chars` field description now state "at least 64".
   `studio/MCP.md` states no per-page bound, so it needed no change.
2. **F2, the AST scan went vacuous on a rename.** `tests/test_web_extract_route.py:344`
   `_scan_failure_literals` returns the function names it found and `:389` asserts
   `found == FAILURE_FUNCTIONS`. Probe: renaming `_fetch_url_raw`, `_read_capped_body` or
   `_validate_and_resolve_host` in the source now raises `AssertionError` naming the missing
   function; before, the scan silently lost its literals. `:367-373` resolves an f-string whose
   first part is an interpolated module constant, so `f"{_RESOLVE_FAILED}: ..."` is scanned: the
   literal count went from 18 to 19 and `engine._RESOLVE_FAILED` is now in the set. `:426` pins
   that.
3. **F3, `check_url_access` was unscanned.** `tests/test_web_extract_route.py:324` reads
   `core/inference/web_access_policy.py` and `:436` `test_every_url_access_refusal_is_classified`
   scans `check_url_access` with the same helper, asserts all six reasons are found (count pinned,
   so a reworded opening cannot make it vacuous), start with `Blocked: ` and classify, and that the
   f-string policy message classifies. `web_access_policy.py` itself was not edited.
4. **F4, overstated comment.** `tests/test_mcp_no_host_paths.py:63-70` and `:174-178` now say what
   the case proves: a requested https URL survives the model's `str` field, and it cannot prove the
   tool ignores the route's echo because the fake answers with the caller's own URLs. Behaviour
   unchanged.
5. **F5, the line-length claim was wrong.** The round-1 note in this file ("new lines are <= 100
   characters") is false: the new lines in `routes/web.py`, `studio_mcp/tools/web.py` and the new
   test files run 101 to 137 characters (measured: `awk 'length > 100'`). `E501` is not selected so
   nothing fails. The lines were not reflowed, by instruction.
6. **F6, duplicated URL-char constant.** No `studio_mcp` tool module imports a route module (checked:
   zero hits for `from routes` under `studio_mcp/`), and importing one would pull FastAPI routing and
   auth into the MCP server's import path, so `studio_mcp/tools/web.py:19-24` keeps `8192` and now
   says the route's value is authoritative, that this copy must match, and what a lowered knob does
   (a forwarded 422 that becomes a `ToolError`).
7. **F7, the new top bound had no refusal case.** `tests/test_mcp_tool_contract.py:75` adds
   `max_chars = 16001` to `REFUSED`, so the `le` is pinned by the tool refusing, not only by the
   schema dump.
8. **F8, the env reload assumes a clean environment.** One-line comment at
   `tests/test_web_extract_route.py:176`; no code change.

Lint: `scripts/enforce_kwargs_spacing.py` on all seven changed Python files -> exit 0, no
adjustments. `.venv/bin/ruff check --select E9,F63,F7,F82` on the same seven -> All checks passed.
Stock `ruff format` not run.

Tests, repo venv, from `studio/backend`, all on the final state:

```
pytest tests/test_web_extract_route.py tests/test_mcp_tools_web.py tests/test_mcp_server.py \
       tests/test_mcp_tool_contract.py tests/test_mcp_no_host_paths.py tests/test_browser_routes.py \
       -q --tb=short -n 4 --dist loadgroup --timeout=330
-> 358 passed, 3 warnings in 16.09 s

same six files, reversed order, -n 0 --timeout=330
-> 358 passed, 3 warnings in 28.17 s

pytest tests/test_mcp_*.py -q --tb=short -n 4 --dist loadgroup --timeout=330   (67 files)
-> 2105 passed, 5 skipped, 19 warnings in 88.64 s

pytest tests/test_web_fetch_extraction.py -q --tb=short --timeout=330   (serial)
-> 1457 passed in 25.61 s
```

The 3 warnings are again the pre-existing FastMCP `ToolAnnotations.*Hint` deprecations at
`tests/test_mcp_server.py:57-61`.

Not done / limits: the six policy reasons are scanned but `hostname_allowed`'s own refusals are
still not (they do not reach the caller as an error string). The MCP copies of the URL-length and
page-minimum constants are still copies; only the page minimum is tied by a test. F9 (page text that
merely starts with a `(` prefix being read as an error) and F10 (`INSTRUCTIONS` does not mention
`web_extract`) were accepted unchanged. No live server was started, so no HTTP or MCP check of the
new 422/refusal was run outside pytest.
