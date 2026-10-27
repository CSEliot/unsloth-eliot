# Task 0 live test, round 2: `web_extract` after fix round 1

Re-run of the live test in `agents/task0_livetest.md` against uncommitted work on branch
`hermes-web-tools`, now including fix round 1 (reserved truncation notice, MCP URL length bound,
env floors). A separate Studio backend was started from the repo `.venv` on `127.0.0.1:8889` with the
existing dev home, exercised over HTTP and MCP, and stopped. No repo source or test file was edited;
the only file written in the repo is this report.

The user's live Studio on `:8888` was not sent any request other than unauthenticated
`GET /openapi.json` probes (before, after run 1, and once more mid-run). It answered `200` each time
up to 22:11:30 +03, but it was **found stopped afterwards**; see "Live Studio :8888 …" under Safety.
That is not something this test caused, but it is the one open item in this report.

## Environment

| Item | Value |
|---|---|
| Interpreter | `/home/cseliot/1-Development/2-Variety/unsloth-eliot/.venv/bin/python` (Python 3.13.15) |
| Command | `.venv/bin/python studio/backend/run.py --host 127.0.0.1 --port 8889 --api-only` |
| Env vars | `UNSLOTH_STUDIO_HOME=/home/cseliot/.unsloth-dev/studio-8889`, `UNSLOTH_STUDIO_ENABLE_MCP=1`, `UNSLOTH_DISABLE_DIFFUSERS_AUTOREPAIR=1` |
| Env verification | `tr '\0' '\n' < /proc/428422/environ \| grep ^UNSLOTH` shows exactly those three names, no `UNSLOTH_HOME`, no `UNSLOTH_LLAMA_CPP_PATH`; same for the second instance, PID 433170 |
| Host binding | `127.0.0.1:8889` only (`ss -ltnp`: `LISTEN 127.0.0.1:8889 users:(("python",pid=428422,fd=23))`); no `0.0.0.0`, no LAN address |
| Home used | the existing `/home/cseliot/.unsloth-dev/studio-8889` (studio.db, auth.db, logs, cache all reused) |
| Dev key | `/home/cseliot/.unsloth-dev/studio-8889.key`, mode 600, unchanged (mtime 21:52). It validated against the new process, so no new key was minted. Never printed; read into a variable or file-read per command |
| Cold start | startup begin 19:09:01.011Z, FastAPI imported 2701.8 ms, pre-uvicorn setup 2748.5 ms, first `GET /openapi.json` 200 at ~19:09:07Z (about 6 s wall including 3.07 s client wait) |
| `/api/web/extract` in dev openapi | yes |
| Installs into the repo venv | none |
| `8889` free before start | yes, `ss -ltnp` showed no listener for 8889 |

Two dev processes were started in the session: PID 428422 (main matrix, stopped at 22:11:00) and PID
433170 (a 30 s edge probe for the small-`max_chars` case, stopped at 22:12:10). Both were the same
command line, env and binding, both logged into the existing `/home/cseliot/.unsloth-dev/studio-8889.log`
(append, banner line per run). Neither spawned children (`ps --ppid` empty), so no llama-server and no
cloudflared.

## Results

All HTTP calls: `POST http://127.0.0.1:8889/api/web/extract`, `Authorization: Bearer <dev key>`.
Milliseconds are wall clock in the client (httpx). Server-side `process_time_ms` agreed within a few ms.

| Case | Expected | Status | ms | text length | Verdict |
|---|---|---|---|---|---|
| 5a Wikipedia | Markdown article text, <= 16000 | 200 | 644 | 15985 | PASS |
| 5b GitHub repo root | README text | 200 | 461 | 3382 | PASS |
| 5c sbox.game | per-URL "no readable text" | 200 | 938 | 0 | PASS |
| 5d arXiv PDF | PDF text, not an error | 200 | 3610 | 15985 | PASS |
| 5e five URLs (run-1 order) | order kept, each <= 4000, total <= 20000 | 200 | 2814 | 156 / 3985 / 3382 / 0 / 3985, total 11508 | PASS |
| 5e five URLs (rotated order) | same | 200 | 2816 | 3985 / 3382 / 0 / 3985 / 156, total 11508 | PASS |
| 5f SSRF 127.0.0.1:8888 and 192.168.1.21:8888 | per-URL Blocked errors, no connection | 200 | 11 | 0 / 0 | PASS |
| 5g no Authorization | 401 | 401 | 6 | n/a | PASS |
| 5h six URLs | 422 | 422 | 7 | n/a | PASS |
| NEW i two dead domains | per-URL `Failed to resolve host…`, text empty | 200 | 25033 | 0 / 0 | PASS |
| NEW j two URLs | each <= 10000 | 200 | 3702 | 9985 / 9985, total 19970 | PASS |
| extra k `max_chars=1200` | <= 1200 | 200 | 517 | 1185 | PASS |
| extra edge `max_chars=1` and `max_chars=10` | <= max_chars | 200 | n/a | 37 / 37 | **FAIL (bug 1)** |

Per-case detail:

- 5a: `# S&box\n\n* [Español](https://es.wikipedia.org/wiki/S%26box)\n* [Français](https://fr.wikipedia.org/wi…`.
  Tail is `…\n\n... (truncated, 23139 chars total)`, i.e. the notice is now inside the cap instead of
  past it. 15985 = 16000 - 51 (reserve) + 36 (actual notice).
- 5b: `README of https://github.com/Facepunch/sbox-public (fetched via the GitHub README API):\n\n<div align=…`, 3382 chars, whole page, no truncation.
- 5c: one page, `text = ""`, `error = "(page returned no readable text)"`.
- 5d: `## Page 1\n\nProvided proper attribution is provided, Google hereby grants permission to\nreproduce the…`, 15985 chars, truncated cleanly with the same notice.
- 5e (both orders): order preserved exactly as requested (`order_ok = true` for each page's `url`).
  Only the two long pages were cut (Wikipedia and arXiv at 3985 = 4000 - 51 + 36); GitHub came back
  whole at 3382; example.com 156; sbox.game 0 with its error in the same 200. Total 11508, far under
  20000 because short pages are not padded.
- 5f: one 200 with two pages, both `text = ""`:
  - `http://127.0.0.1:8888/` -> `Blocked: refusing to fetch non-public address 127.0.0.1.`
  - `http://192.168.1.21:8888/` -> `Blocked: refusing to fetch non-public address 192.168.1.21.`

  11 ms for both, against 2.8 s for the real five-URL fetch, so nothing was dialed. The live Studio
  stayed healthy at that point (its next probe, at 22:11:30, returned 200).
- 5g: `401 {"detail": "Not authenticated"}`.
- 5h: `422`, `List should have at most 5 items after validation, not 6`.
- NEW i: both in one 200 with `text = ""`:
  - `https://this-domain-does-not-exist-unsloth-test.invalid/` -> `Failed to resolve host: [Errno -2] Name or service not known`
  - `https://nonexistent-host-7f3a9c.example.com/` -> `Failed to resolve host: [Errno -3] Temporary failure in name resolution`

  The 25 s for the pair is resolver time inside the engine (the 60 s call deadline was not hit; each
  URL keeps its own 30 s fetch timeout). Worth knowing: an unresolvable host costs most of the call's
  latency even though the failure is cheap.
- NEW j: Wikipedia 9985 and arXiv 9985, total 19970 <= 20000. This is the case the previous run
  overshot at 10036.
- extra k: `max_chars=1200` -> 1185, so a caller-supplied smaller cap is honoured exactly (cap - 51 + 36).
- extra edge: `max_chars=1` -> 37 chars, `max_chars=10` -> 37 chars, both tail
  `#\n\n... (truncated, 23091 chars total)`; `max_chars=60` -> 45 chars. See bug 1.

### MCP

Client: `fastmcp` from the repo `.venv` (`Client(StreamableHttpTransport("http://127.0.0.1:8889/mcp/", headers = {"Authorization": "Bearer <dev key>"}))`).
Note for anyone re-running: fastmcp 4.x renamed the result fields, so the harness has to read
`tool.input_schema`, `tool.output_schema` and `result.structured_content`. The first attempt of my
script used the old `inputSchema` / `structuredContent` names and raised `AttributeError` in the
client; that is a harness issue, not a product one.

| Check | Result | Verdict |
|---|---|---|
| `list_tools` | 24 tools, 48 ms | PASS |
| `web_extract` present | yes | PASS |
| annotations | `read_only_hint = true`, `open_world_hint = true`, `destructive_hint = null`, `idempotent_hint = null` | PASS |
| `urls` schema | `{"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "string", "minLength": 1, "maxLength": 8192}}` | PASS |
| `max_chars` schema | `{"anyOf": [{"type": "integer", "minimum": 1, "maximum": 16000}, {"type": "null"}], "default": null}` | PASS |
| required | `["urls"]` | PASS |
| output schema | declared (`additionalProperties`, `properties`, `title`, `type`) | PASS |
| `call_tool("web_extract", {urls: [wikipedia, dead]})` | `is_error = false`, 531 ms, one `TextContent` block of 10324 chars holding the JSON `{"pages": [...]}` | PASS |
| per-page shape | page 1 `https://en.wikipedia.org/wiki/S%26box`, text 9985, `error = null`, starts `# S&box\n\n* [Español]…`; page 2 `https://this-domain-does-not-exist-unsloth-test.invalid/`, text 0, `error = "Failed to resolve host: [Errno -2] Name or service not known"` | PASS |
| text block vs structuredContent | identical page list from both (9985/0, same URL, same error) | PASS |
| initialize without the key | `401 {"detail": "Unsloth Studio MCP needs an Unsloth Studio API key (sk-unsloth-…). Create one in Settings > API."}` | PASS |

The MCP call went through the HTTP route: the dev log shows the matching `POST /api/web/extract 200`.

## Comparison with the first live test

| Case | Run 1 | Run 2 | Change |
|---|---|---|---|
| 5a Wikipedia | 16036 (over 16000) | 15985 | overrun gone |
| 5b GitHub | 3382 | 3382 | same |
| 5c sbox.game | 0 + error | 0 + error | same |
| 5d arXiv PDF | 15999 | 15985 | 14 chars less, still inside 16000 |
| 5e five URLs | 4036 each for the two long pages (over 4000), total 12090 | 3985 each, total 11508 | overrun gone |
| 5f SSRF | Blocked, 12 ms | Blocked, 11 ms | same |
| 5g / 5h | 401 / 422 | 401 / 422 | same |
| two long URLs | 10036 each (over 10000) | 9985 each, total 19970 | overrun gone |
| MCP tool count | 24 | 24 | same |
| MCP call | 682 ms, wikipedia 10036 | 531 ms, wikipedia 9985 | inside the cap now |

Bug 1 of the first report (per-page cap exceeded by the truncation suffix) is fixed for every default
split and for any `max_chars >= 52`. It is not fixed below that, see bug 1 below.

## Safety

Checked on PID 428422 before shutdown, and again on PID 433170.

- `ls -l /proc/428422/fd`: only `/home/cseliot/.unsloth-dev/studio-8889.log`,
  `<dev home>/logs/server/server-20261026-220900-pid428422.log`,
  `<dev home>/studio.db{,-wal,-shm}`, `<dev home>/auth/auth.db{,-wal,-shm}`, `/dev/null`, sockets and
  an eventpoll. **No descriptor under `~/.unsloth/`.** For 433170:
  `ls -l /proc/433170/fd | grep -c '\.unsloth/studio'` -> `0`.
- `lsof -p 428422`: the same dev-home paths plus the repo directory, the uv python binary and
  `/usr/lib/...` bits. Nothing under `~/.unsloth`, nothing under `~/.cache`.
- `ps --ppid 428422` and `ps --ppid 433170`: empty; no llama-server, no cloudflared, no tunnel.
- `find ~/.unsloth -newer /home/cseliot/.unsloth-dev/marker2 -printf '%p %TH:%TM:%TS\n'` returned three
  paths:
  - `/home/cseliot/.unsloth/studio/logs/server/server-20261025-233704-pid6173.log`
  - `/home/cseliot/.unsloth/studio/logs/backend-backend-1792960623605-2-s01.log`
  - `/home/cseliot/.unsloth/studio/rag`

  Attribution: the live app's own writes. Two of the names carry the live PID 6173, and after the dev
  server was stopped and the machine sat idle for 25 s the same three paths were updated again
  (22:10:19/22:10:40 -> 22:11:30/22:11:10) with no dev process alive. The dev process never held a
  descriptor into `~/.unsloth`.
- Shared caches: `~/.cache/huggingface` has no entry newer than the marker; `~/.cache/unsloth` does not
  exist. No shared-cache writes.
- Requests actually served by the dev process (from its own log, `request_completed` lines):
  12 x `POST /api/web/extract 200`, 1 x 401, 1 x 422, 8 x `/mcp/ 200`, 1 x `/mcp/ 401`,
  1 x `GET /openapi.json 200`. All of them came from my client; the 401s are the deliberate no-key
  cases. Zero `"level": "error"` lines in either run.
- Shutdown: SIGINT to 428422, exited after 3 s, `ss` shows 8889 closed, no SIGTERM needed. Same for
  433170 (3 s, then `8889 closed`). Both logs end with `Application shutdown complete` /
  `Finished server process`. Unlike run 1, no `Task was destroyed but it is pending!` appeared this
  time, so that warning is intermittent rather than a new regression.

### Live Studio :8888 was found stopped at the end of the run

What the probes say:

| Time (+03) | Probe | Result |
|---|---|---|
| ~22:08 | `GET /openapi.json` before any dev process existed | 200, 1,098,354 bytes, 0.018 s |
| 22:11:30 | `GET /openapi.json` after the first dev process was stopped | 200, 1,098,354 bytes, 0.017 s |
| 22:12:13 | `GET /openapi.json` after the second dev process was stopped | connection refused (`000`) |

`ss` and `ps` then show no listener on 8888 and no PID 6173. The live app was not a bare process: it
was the user systemd unit `app-Unsloth@autostart.service`, `ExecMainPID 3858`, started
2026-10-25 23:37:01.109, which reports `ExecMainCode=1`, `ExecMainStatus=0` (exited normally, status 0)
at `ExecMainExitTimestamp=Mon 2026-10-26 22:11:54.142518 +03`. The unit is now `inactive (dead)`,
`NRestarts=0`, so nothing restarted it.

Why I do not think this test killed it: the live app's main process exited at 22:11:54.142518, and the
second dev process wrote its first log line (`run_server startup begin … pid=433170`) at
22:11:54.256278, 114 ms later; the dev process's own port was 8889, it held no descriptor into
`~/.unsloth`, and the only signals I sent all session were `kill -INT 428422`, `kill -INT 433170` and
one stand-by `kill -TERM` path that was never used (both exited on SIGINT). The first dev process was
fully stopped at 22:11:00 and the live app still answered 200 at 22:11:30, which also rules out the
dev shutdown path as the trigger. A normal exit with status 0 is what quitting the desktop app looks
like. I did not read the live app's log or any file under `~/.unsloth/studio` (out of bounds for this
task), so the trigger is unattributed from my side. **The user should relaunch Studio**; I did not
start it, since touching the live instance was forbidden.

## Bugs

1. **Small caller-supplied `max_chars` still overflows (residual of run-1 bug 1).** The route reserves
   the truncation notice when it computes a page budget (`routes/web.py:82-88, 139-146`), but the
   budget floor is `max(1, cap - TRUNCATION_NOTICE_RESERVE)`, so for `max_chars` below 52 the engine is
   handed 1 and appends its 36-character notice after slicing, giving 37 characters back for a 1- or
   10-character request. Live evidence: `max_chars=1` -> 37 chars, `max_chars=10` -> 37 chars, both
   ending `#\n\n... (truncated, 23091 chars total)`; `max_chars=60` -> 45 and `max_chars=1200` -> 1185
   are inside their caps. The MCP schema and the route both allow `max_chars = 1`
   (`ge = 1`), so a client that asks for 10 and budgets 10 gets 37. Either clamp the schema to a
   usable minimum (52) or return the engine's text trimmed to the requested cap.
2. **Not a bug, worth recording: the reserve under-fills every truncated page by 15 characters.**
   `TRUNCATION_NOTICE_RESERVE` is 51 (computed with a 20-digit total) while the real notice is 36, so
   a 16000-cap page arrives at 15985, a 4000-cap page at 3985, a 10000 share at 9985. Safe direction,
   costs a little budget; the ceilings in the docstring, the tool description and `studio/MCP.md` are
   now true.
3. **Fixed, confirmed live:** run-1 bug 1 (page text exceeding its stated cap) no longer reproduces on
   any of the tested splits: single URL 15985/16000, two URLs 9985/10000 each with total 19970/20000,
   five URLs 3985/4000 each.
4. **Observation, not a bug:** a pair of unresolvable hosts costs ~25 s of resolver time inside a
   single 200 response. That is well under `EXTRACT_DEADLINE_S` (60) and each fetch has its own 30 s
   timeout, but it is the slowest realistic failure mode a caller will meet.

## What was not re-tested

- No model was loaded, so `_dense_char_limit`'s model-loaded shrink path was not exercised; all caps
  above are the unmetered ones.
- No 503 / in-flight-cap behaviour was provoked (would need 5 concurrent 5-URL calls at 24 slots), no
  deadline timeout, no `max_chars` above 16000 through MCP (schema refuses it), no tunnel, no LAN bind.
- Hermes was not wired to the 8889 MCP endpoint; nothing outside this repo and the dev home was edited.

## Artefacts left in place

- Dev home `/home/cseliot/.unsloth-dev/studio-8889/`, log `/home/cseliot/.unsloth-dev/studio-8889.log`
  (both runs appended, banner lines `=== run 2 start` and `=== run 2b edge probe`), key
  `/home/cseliot/.unsloth-dev/studio-8889.key` (mode 600, still valid).
- Client records in `/home/cseliot/.hermes/cache/scratch/`: `http_results2.json`,
  `http_results2_e2.json`, `http_results2_edge.json`, `mcp_results2.json`, scripts `livetest2_http.py`,
  `livetest2_e2.py`, `livetest2_mcp.py`, `livetest2_mcp_schema.py`, `livetest2_edge.py`. Excerpts only,
  no key.
