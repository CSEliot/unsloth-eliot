# Code review round 2: `web_extract` fix round 1 (branch `hermes-web-tools`, uncommitted)

Verdict: **APPROVE WITH FIXES**. All nine claimed fixes are present in the code and are backed by
tests that pass. The concurrency and classification work is sound. Two things still need a decision:
a caller `max_chars` under 37 is not honoured (the engine's truncation notice is appended past it),
and two of the new tests do less than their comments say (the AST scan goes silently blind if a
scanned function is renamed, and the host-path case cannot distinguish the caller's URL from the
route's). Neither is a blocker; both are cheap.

Scope: `git status` of the branch, the full text of `routes/web.py`, `studio_mcp/tools/web.py`, the
five test files, the touched hunks of `outputs.py`, `forward.py`, `mcp_server.py`, `main.py` and
`studio/MCP.md`, plus the engine (`core/inference/tools.py`, `core/inference/web_access_policy.py`,
unchanged) for the notice and failure literals. Probes under `/home/cseliot/.hermes/cache/scratch`.
No repo file other than this report was written. No server, no network, no git write.

## Fix status

| # | Claim | Status | Evidence |
|---|-------|--------|----------|
| 1 | Failure prefix list covers the resolver and decoder strings; AST test proves the list is complete | VERIFIED, with the proof claim overstated (F3) | `routes/web.py:101-116` now has `Failed to resolve host`, `Failed to decode the compressed response: `, `(compressed content exceeds ` and `(content exceeds `. `_RESOLVE_FAILED = "Failed to resolve host"` at `tools.py:126`, returned unwrapped at `:16524`, `:16527`, `:16709` and passed straight through by `_fetch_url_raw:17177-17178`, so the prefix is what catches it. `FAILURES` at `tests/test_web_extract_route.py:259-277` holds 12 strings including the two resolver forms. Probe: the AST scan collects exactly the 18 literals the report claims, and `_failed()` classifies all 18. The route can still receive six `Blocked: ...` strings from `check_url_access` (`web_access_policy.py:134-149`) which no test scans, but all six are classified today (F3). |
| 2 | Per-page overrun fixed by reserving the notice length | VERIFIED for every default share; a caller cap under 37 still overruns (F1) | `routes/web.py:87-88` computes 51 characters; `_page_budget:139-146` subtracts it and floors at 1. Probe: caps sent to the engine are 15949 / 9949 / 6615 / 3949 for 1 / 2 / 3 / 5 URLs. Real notice for a 40,000 character page is 36 characters (`tools.py:18107`), so the reserve is 15 over. End-to-end with the real `_truncate_page_text`: one URL returns 15985 characters, five return 3985 each (19,925 total), so the 16000 / 10000 / 4000 / 20000 ceilings hold for the default path. `test_no_page_arrives_over_its_share` and `test_the_real_engine_notice_still_fits_the_share` pin it, and the second one fails if the engine's notice format grows. |
| 3 | Description says "up to", mentions the model-window shrink | VERIFIED | `studio_mcp/tools/web.py:45-55` ("1 URL gets up to 16000, 2 get up to 10000 each, 5 get up to 4000 each", "these are ceilings, not quotas", the model-window sentence). `studio/MCP.md:79-89` mirrors it. No remaining exact-ceiling claim in the tool schema. |
| 4 | Env knobs floored, in-flight floored at the URL count | VERIFIED | `_env_int(name, default, minimum = 1)` at `routes/web.py:33-44`; `EXTRACT_IN_FLIGHT` uses `minimum = MAX_EXTRACT_URLS` (`:74-76`); every other knob gets the default floor. `test_env_tunables_are_clamped_to_a_usable_minimum` (`tests/test_web_extract_route.py:142-174`) sets eight overrides low, reloads, asserts `EXTRACT_IN_FLIGHT == MAX_EXTRACT_URLS` (2 becomes 5) and every constant >= 1. Test isolation caveat in F8. |
| 5 | Comment near `MAX_EXTRACT_PAGE_CHARS` corrected | VERIFIED | `routes/web.py:59-62` now says the engine truncates to whatever cap it is handed and only uses its own constant when `max_chars is None`, which matches `_fetch_page_text:18124-18125`. |
| 6 | Slot release asserted on success, failure, refusal and timeout | VERIFIED | `test_a_full_in_flight_cap_is_a_503:402-415`, `test_a_call_takes_all_its_slots_or_none:418-431` (asserts `_slots_left == 1` after the partial acquire, which is exactly what a deleted release loop at `routes/web.py:193-195` breaks), `test_a_successful_call_gives_its_slots_back:434-443`, `test_an_engine_exception_gives_its_slot_back:446-460`, `test_an_expired_deadline_gives_its_slot_back_when_the_worker_ends:463-479` (0 right after the timed-out response, 1 after the worker ends, then a further call admitted). All pass. |
| 7 | `test_mcp_no_host_paths` narrowed to ignore only `text` | VERIFIED, comment overstates what is proved (F4) | `tests/test_mcp_no_host_paths.py:60-84` serves the case's own URLs unpoisoned and poisons `text` and `error`; `MODEL_TEXT` at `:175-182` ignores only `text`. Probe: `leaks()` with `ignore = {"text"}` reports a sentinel in `url` and stays silent when only `text` carries it, so the field is live again. `error` is still covered because it is poisoned in the fake and must survive `RouteText` scrubbing. |
| 8 | MCP URL length bound added and documented | VERIFIED | `studio_mcp/tools/web.py:19-35`: `MAX_EXTRACT_URL_CHARS = 8192`, `urls` items bounded, description says "each at most 8192 characters". `tests/test_mcp_server.py:107-109` pins `maxLength 8192` and `maxItems 5` with an exact-type assertion, `tests/test_mcp_tool_contract.py:69` refuses a 9,000 character URL. All pass. Duplicated env-tunable constant, see F6. |
| 9 | Installation-wide cap documented | VERIFIED | `routes/web.py:71-73` and `studio/MCP.md:87-89` both state the single installation-wide cap, the 503 for other callers, and that a per-account share is the next step. |

No drive-by edits: the tracked-file diff is still `outputs.py` (two models), `forward.py` (one
prefix plus comment), `mcp_server.py` (two lines), `main.py` (two lines), the two test files, and
`studio/MCP.md`. `core/inference/tools.py` and `routes/browser.py` are untouched.

## New findings

| # | Severity | Path:line | Finding |
|---|----------|-----------|---------|
| F1 | MINOR | `routes/web.py:145-146`, `routes/web.py:126`, `studio_mcp/tools/web.py:39` | A caller cap below 37 is not honoured: `_page_budget` floors the engine cap at 1, the engine appends its 36 to 38 character notice past that 1 character, and the page comes back over the cap. Live probe through the real route with the real `_truncate_page_text`: `max_chars = 1`, `10`, `36` return 37 characters each (36, 27 and 1 characters over), status 200, `error = null`. The same floor also makes caps 37 to 59 return only 37 characters, so most of the range between the notice length and the reserve is erased. Defaults are unaffected (share >= 4000). This is the edge the round-1 reserve introduced. Options, in order of preference: raise the floor to 64 in both schemas (`ge = 64` in `ExtractRequest` and in the MCP `max_chars` Field), so the overshoot becomes a 422 instead of a silent breach of the field's own description; or document that a cap under the notice length can come back up to 38 characters longer; or trim in `_fetch_one` after the `_failed()` check, which truncates the notice and reads badly. |
| F2 | MINOR | `tests/test_web_extract_route.py:295-320, 346-353` | The AST test goes silently vacuous if a scanned function is renamed or moved. `_failure_literals()` filters by `node.name in FAILURE_FUNCTIONS` with no assertion that those names were found, and its only positive control covers two strings that both live in `_read_capped_body`. Probe: dropping `_fetch_url_raw` from the set loses 12 of the 18 literals while the positive control still passes; the same holds for `_fetch_budget_exceeded`, `_truncate_page_text` and `_validate_and_resolve_host`. `test_every_failure_prefix_is_a_literal_in_the_engine` also has no way to see a renamed function, because the strings are still somewhere in the source. Fix: collect the names actually found and assert `found_names == FAILURE_FUNCTIONS` before scanning. Second, smaller point: the scan only accepts a leading `ast.Constant`, so the resolver's `f"{_RESOLVE_FAILED}: ..."` returns are invisible to it; they are covered only by the prefix test, which is a bare substring check against the whole file. |
| F3 | NIT | `tests/test_web_extract_route.py:280-291`; `core/inference/web_access_policy.py:134-149` | `check_url_access` is on the fetch path (`tools.py:18134-18136` returns its reason verbatim) but is not in `FAILURE_FUNCTIONS` and lives in another module, so a reword there ("Blocked: URL is empty." to "Denied: ...") would unmask an error as page text with no test failing. All six current reasons start with `Blocked: ` and classify correctly (probe). Either add the function and its module to the scan, or drop the sentence in the build report that says the AST test proves the `check_url_access` leg. |
| F4 | NIT | `tests/test_mcp_no_host_paths.py:63-84, 172-174`; `studio_mcp/tools/web.py:66, 71` | The comment says the case checks "the tool's echo of the caller's URL", but the tool prefers the route's `url` (`page_url if isinstance(page_url, str) and page_url else url`) and the fake answers with the caller's own URLs, so a regression to trusting `row["url"]` would still pass. What the case does prove is that a str field cannot carry a sentinel past the model, which is worth keeping. Either reword the comment to say the route echoes the request URL, or make the tool return the requested `url` unconditionally and let the fake answer with a poisoned URL, which would then test the claim. |
| F5 | NIT | `routes/web.py:47, 90, 93, 158, 188`; `studio_mcp/tools/web.py:41, 47, 53, 54` | The build report's lint note ("new lines are <= 100 characters") is not true. Those new lines run 101 to 127 characters. `E501` is not selected so nothing fails, and round 1 filed the same point as a NIT; only the report's statement needs correcting, or the lines rewrapped. |
| F6 | NIT | `studio_mcp/tools/web.py:19-21` | `MAX_EXTRACT_URL_CHARS = 8192` is a hardcoded copy of `routes/web.py:54`, which is env-tunable. A deployment that lowers `UNSLOTH_STUDIO_WEB_EXTRACT_URL_CHARS` makes the tool forward a URL the route answers 422 for (`route_json` turns that into a `ToolError`), and one that raises it makes the tool refuse a URL the route would accept while its description says 8192. Same shape already exists for `MAX_EXTRACT_URLS`; noting it because fix 8 added a second copy. |
| F7 | NIT | `tests/test_mcp_tool_contract.py:64-70` | The new `le = 16000` on the MCP `max_chars` is a new refusal path with no case: `REFUSED` covers `max_chars = 0` but not `16001`, so the bound is only pinned by the schema dump, not by the tool refusing. One more tuple would close it. |
| F8 | NIT | `tests/test_web_extract_route.py:171-174` | The "leave the module as it was found" reload deletes the eight overrides and reloads, so on a machine with any of those knobs exported the module is left at the defaults while the environment still carries the values. Harmless for this suite (it already assumes a clean environment, and the whole file passes with `UNSLOTH_STUDIO_WEB_EXTRACT_MAX_URLS=3` set, but only because this test resets the module mid-file), and the second reload does keep later tests on the defaults they expect. Worth a comment rather than a code change. xdist is fine: the reload touches one worker's module object only, and nothing outside `routes/web.py` holds these constants. |
| F9 | NIT | `routes/web.py:101-116` | The `(` prefixes are matched against page text as well as failures, so a page whose text literally begins with `(content exceeds ` (new, defence only) or `(PDF ` would be reported as an error with the content dropped. Same class as the pre-existing entries and vanishingly unlikely; recorded so it is a decision. |
| F10 | NIT | `studio/backend/mcp_server.py:21-22` (wiring) and the tool description | `INSTRUCTIONS` still does not mention `web_extract` (the build report says it was skipped to stay under the 1900 character assertion). Agents that read the instructions rather than the tool list will not know it exists; the description itself is good. Not new from this round. |

## Test results

All commands from `studio/backend` with `../../.venv/bin/python -m pytest`, real output:

```
tests/test_web_extract_route.py tests/test_mcp_tools_web.py tests/test_mcp_server.py \
tests/test_mcp_tool_contract.py tests/test_mcp_no_host_paths.py \
-q --tb=short -n 4 --dist loadgroup --timeout=330
-> 214 passed, 3 warnings in 13.30s

same five files, -p no:randomly -n 0 --timeout=330
-> 214 passed, 3 warnings in 23.32s

same five files reversed (no_host_paths, tool_contract, server, tools_web, route), -n 0
-> 214 passed, 3 warnings in 23.76s

UNSLOTH_STUDIO_WEB_EXTRACT_MAX_URLS=3, tests/test_web_extract_route.py, -n 0
-> 52 passed in 4.75s
```

The three warnings are the pre-existing `ToolAnnotations.openWorldHint` / `readOnlyHint` /
`destructiveHint` deprecations at `tests/test_mcp_server.py:57-61`; line 57 is the edited line but it
uses the same deprecated alias as its neighbours. Nothing touched the network.

Note on order dependence: `pytest-randomly` is not installed in the venv, so `-p no:randomly` is a
no-op and the "random" axis is untested; I substituted a reversed file order, which also passes.
Within `test_web_extract_route.py` the env-reload test runs before the cap tests in file order, so
the state it leaves is exercised by 40 later tests in the same process.

## Probes (throwaway, under `/home/cseliot/.hermes/cache/scratch`, no repo file touched)

- `probe_budget_r2.py`: reserve is 51 for 20 digits, real notice is 36 to 38; `_page_budget` matrix
  15949 / 9949 / 6615 / 3949; caller caps 1, 10, 40, 51, 52, 60, 100, 500 against the real
  `_truncate_page_text`, showing the overrun under 37 and the 1 character slice for 37 to 59.
- `probe_route_cap.py`: the same matrix through `POST /api/web/extract` with a real
  `_truncate_page_text` engine, all 200s: `max_chars = 10` returns 37 characters, default returns
  15985, five URLs return 3985 each (19,925 total).
- `probe_ast.py` / `probe_ast2.py`: 18 literals collected, one owning function each, all classified
  by `_failed()`; rename simulation showing 12 of 18 lost with the positive control still green;
  the six `check_url_access` strings outside the scan.
- `probe_misc.py`: `_html_to_markdown` tolerates `max_span_chars` 0 and 1; `leaks()` finds a sentinel
  in `url` with `text` ignored, and finds nothing when only `text` carries it.

Not done: no mutation run of the route source (no repo edits were allowed, so the deleted-release
check at fix 6 is confirmed by reading the assertion and the semaphore arithmetic, not by running a
mutant), and no live server, per the constraints.
