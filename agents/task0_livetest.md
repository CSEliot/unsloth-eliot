# Task 0 live test: `web_extract` on a dev Studio backend at 127.0.0.1:8889

First-pass live test of the uncommitted `web_extract` work on branch `hermes-web-tools`. A separate
Studio backend was started from the repo `.venv` with its own home, exercised over HTTP and MCP, then
stopped. The user's live Studio on :8888 (PID 6173, command line
`/home/cseliot/.unsloth/studio/unsloth_studio/bin/python ... unsloth studio --api-only -H 127.0.0.1 -p 8888`)
was not sent any request other than the two allowed `GET /openapi.json` probes, and no repo source or
test file was touched. The only file written in the repo is this report.

## Environment

| Item | Value |
|---|---|
| Interpreter | `/home/cseliot/1-Development/2-Variety/unsloth-eliot/.venv/bin/python` (Python 3.13.15) |
| Command | `.venv/bin/python studio/backend/run.py --host 127.0.0.1 --port 8889 --api-only` |
| Env vars | `UNSLOTH_STUDIO_HOME=/home/cseliot/.unsloth-dev/studio-8889`, `UNSLOTH_STUDIO_ENABLE_MCP=1`, `UNSLOTH_DISABLE_DIFFUSERS_AUTOREPAIR=1` |
| Env verification | `/proc/407213/environ` holds exactly those three (no `UNSLOTH_HOME`, no `UNSLOTH_LLAMA_CPP_PATH` override) |
| Host binding | `127.0.0.1:8889` only, confirmed with `ss -ltnp` (`LISTEN 127.0.0.1:8889 users:(("python",pid=407213,fd=20))`); `--host` defaults to `127.0.0.1` (run.py:3194-3200) |
| Tunnel | none started. `_cloudflare_tunnel_should_start` requires a wildcard host and `not api_only` (run.py:2048-2057); `--cloudflare` was not passed and defaults to None |
| llama.cpp redirect | `unsloth_home()` returned `None` with the dev env (no `UNSLOTH_HOME`, no recorded master root), so run.py:1362-1372 derives `UNSLOTH_LLAMA_CPP_PATH` as `<dev home>/llama.cpp`. Verified by importing `utils.paths.storage_roots` with the dev env: `studio_root = /home/cseliot/.unsloth-dev/studio-8889`, `unsloth_home = None`. No neutralising override was needed |
| Frontend | not served (`--api-only`, no `studio/frontend/dist`); the MCP app is mounted regardless (main.py:1210) |
| Installs into the repo venv | none. No `uv pip install` was needed |

Cold start: process start at 18:51:58Z, uvicorn ready after 3561 ms, first `GET /openapi.json` 200 at
18:52:05Z, i.e. about **7.5 s** wall from exec to first HTTP answer. (`torch warm finished` at
18:52:09.99Z, after HTTP was already serving.) `/api/web/extract` is present in the dev
`/openapi.json`.

Dev API key: minted after startup with `auth.storage.create_api_key(username = storage.DEFAULT_ADMIN_USERNAME,
name = "dev-8889")` (real signature at auth/storage.py:1769-1777; returns `(raw, row)`). The raw key
was written straight to `/home/cseliot/.unsloth-dev/studio-8889.key` with mode 600 (44 bytes
including the newline, `sk-unsloth-` prefix, row `is_internal = 0`). It was never printed and is not in
this report.

## Results

All HTTP calls: `POST http://127.0.0.1:8889/api/web/extract` with `Authorization: Bearer <dev key>`.
Times are wall-clock milliseconds measured in the client process (httpx).

| Case | Expected | Status | ms | Result | Verdict |
|---|---|---|---|---|---|
| 5a Wikipedia | Markdown article text | 200 | 565 | 16036 chars, `# S&box ...` | PASS |
| 5b GitHub repo root | README text | 200 | 471 | 3382 chars, `README of https://github.com/Facepunch/sbox-public (fetched via the GitHub README API)` | PASS |
| 5c sbox.game | per-URL "no readable text" error | 200 | 963 | 0 chars, error `(page returned no readable text)` | PASS |
| 5d arXiv PDF | PDF text, not an error | 200 | 3365 | 15999 chars, `## Page 1 ... **Attention Is All You Need**` | PASS |
| 5e five URLs | order kept, every page <= 4000 chars, one URL <= 16000 | 200 | 3364 | order kept; lengths 156, 4036, 3382, 0, 4036 | **FAIL (cap overshoot, see bug 1)** |
| 5f SSRF 127.0.0.1:8888 and 192.168.1.21:8888 | per-URL Blocked errors | 200 | 12 | `Blocked: refusing to fetch non-public address 127.0.0.1.` and `... 192.168.1.21.` | PASS |
| 5g no Authorization header | 401 | 401 | 7 | `{"detail": "Not authenticated"}` | PASS |
| 5h six URLs | 422 | 422 | 7 | `List should have at most 5 items after validation, not 6` | PASS |

Single-URL budget check: Wikipedia alone returned 16036 chars, i.e. 36 over the 16000 cap. Two URLs
(example.com + Wikipedia) returned 156 and 10036, i.e. 36 over the 10000 share. So the overrun is
exactly the length of the engine's truncation note `\n\n... (truncated, 23139 chars total)`, appended
after the slice in `_truncate_page_text` (core/inference/tools.py:18105-18107). The tail of the
Wikipedia page text in the live response is
`... | People | * Garry Newma\n\n... (truncated, 23139 chars total)`.

SSRF note (case 5f): the engine refuses in `_validate_and_resolve_host` (tools.py:16510-16556), which
runs `getaddrinfo` on the literal and returns `Blocked: ...` before any `create_connection`, so no
socket was opened toward :8888. The 12 ms response for both URLs, against 3.3 s for a real fetch, is
consistent with that.

Case 5f excerpt detail, both pages came back in the same 200 with `text = ""`:

- `http://127.0.0.1:8888/` -> `Blocked: refusing to fetch non-public address 127.0.0.1.`
- `http://192.168.1.21:8888/` -> `Blocked: refusing to fetch non-public address 192.168.1.21.`

### 6. MCP

Client: `fastmcp` 4.1.4 from the repo `.venv`, `fastmcp.Client(StreamableHttpTransport(url, headers = {"Authorization": "Bearer <dev key>"}))`
against `http://127.0.0.1:8889/mcp/`.

| Check | Result | Verdict |
|---|---|---|
| `list_tools` | 24 tools, 60 ms | PASS |
| `web_extract` present | yes | PASS |
| annotations | `readOnlyHint = true`, `openWorldHint = true`, `destructiveHint = null` | PASS |
| input schema | `urls`, `max_chars` | PASS |
| output schema | declared (`title`, `type`, `properties`, `additionalProperties`) | PASS |
| `call_tool("web_extract", {urls: [wikipedia, sbox.game]})` | `is_error = false`, 682 ms, one `TextContent` block of 10310 chars whose body is the JSON `{"pages":[...]}`, plus `structuredContent = {"pages": [...]}` | PASS |
| per-page shape (from structuredContent) | page 1 `url` = the Wikipedia URL, `text` 10036 chars, `error` null, starts `# S&box\n\n* [Español](...)`; page 2 `url` = `https://sbox.game`, `text` 0, `error` = `(page returned no readable text)` | PASS |
| call without the key | HTTP 401 `{"detail": "Unsloth Studio MCP needs an Unsloth Studio API key (sk-unsloth-…). Create one in Settings > API."}`; the fastmcp client surfaces it as `MCPError: Server returned an error response` | PASS |
| same endpoint with the key (raw initialize) | 200, `event: message`, protocolVersion `2025-06-18` | PASS |

The MCP result is dual-emitted (text block + structuredContent) as the research note predicted, so a
Hermes client reads the text rendering.

## Safety check

Run before shutdown, on the dev process PID 407213.

- `ls -l /proc/407213/fd`: fds point only at `/home/cseliot/.unsloth-dev/studio-8889.log`,
  `<dev home>/logs/server/server-20261026-215159-pid407213.log`, `<dev home>/studio.db{,-wal,-shm}`,
  `<dev home>/auth/auth.db{,-wal,-shm}`, plus anonymous sockets. **No fd target under
  `~/.unsloth/`.** `lsof -p 407213` agrees (only dev-home paths and `/usr/lib/.../gconv-modules.cache`).
- `ps --ppid 407213`: no child processes, so no llama-server/cloudflared was spawned.
- `find ~/.unsloth -newer /home/cseliot/.unsloth-dev/marker -printf '%p\n'` (paths only) returned
  three paths:
  - `/home/cseliot/.unsloth/studio/logs/server/server-20261025-233704-pid6173.log`
  - `/home/cseliot/.unsloth/studio/logs/backend-backend-1792960623605-2-s01.log`
  - `/home/cseliot/.unsloth/studio/rag`

  Attribution: **all three are the live app's own writes, not ours.** The server log is named for the
  live PID 6173, and after the dev server was stopped and the machine left idle for ~25 s the same
  three paths were updated again (mtimes 21:53:29 -> 21:54:10/21:54:12) with no dev process running.
  The dev process never held a descriptor into `~/.unsloth`, and `--api-only` plus a distinct home
  rules out the auth/rag DBs it opened.
- Live app still healthy after the test: `GET http://127.0.0.1:8888/openapi.json` ->
  `200`, 0.018 s (same as the pre-test probe, which was also 200/1,098,354 bytes).
- Shared caches: `~/.cache/unsloth` does not exist; `~/.cache/huggingface` exists but has no entry
  newer than the marker. No shared-cache writes.
- Everything the dev run wrote is inside `/home/cseliot/.unsloth-dev/studio-8889`
  (`auth/`, `studio.db`, `rag/`, `logs/`, `cache/`, `compiled_cache/`, `exports/`, `outputs/`,
  `runs/`, `assets/`, `audio/`, pid and marker files). Nothing else outside it except the three files
  named above, which are the live app's.

Shutdown: SIGINT to PID 407213, exited after ~4 s, `ss` shows 8889 closed. The log ends with
`Application shutdown complete` / `Finished server process [407213]`, preceded by
`Task was destroyed but it is pending!` for an `sse_starlette._shutdown_watcher` task. No SIGTERM was
needed.

## Artefacts left in place

- Dev home: `/home/cseliot/.unsloth-dev/studio-8889/`
- Log: `/home/cseliot/.unsloth-dev/studio-8889.log`
- Dev key (mode 600, still valid for a future 8889 run): `/home/cseliot/.unsloth-dev/studio-8889.key`
- Marker: `/home/cseliot/.unsloth-dev/marker`
- Client-side test records: `/home/cseliot/.hermes/cache/scratch/{http_results.json,mcp_results.json}`
  (excerpts only, no key)

## Proposed Hermes wiring (NOT applied)

Hermes was not touched; changing `~/.hermes` needs the user's approval. The snippet to add, with the
key referenced by env var name:

```yaml
mcp_servers:
  unsloth_studio_8889:
    url: "http://127.0.0.1:8889/mcp/"
    headers:
      Authorization: "Bearer ${UNSLOTH_STUDIO_API_KEY_8889_DEV}"
```

`UNSLOTH_STUDIO_API_KEY_8889_DEV` would be exported in the shell or the Hermes env, its value being the
contents of `/home/cseliot/.unsloth-dev/studio-8889.key`. The 8888 entry would stay as it is; adding
this one gives 24 tools under `mcp__unsloth_studio_8889__*` alongside the existing 23.

## Bugs found

1. **Per-page character cap is exceeded by the truncation suffix (route + MCP contract).**
   `routes/web.py` computes the page budget and passes it as `max_chars`, but the engine appends
   `\n\n... (truncated, N chars total)` after slicing to that cap (core/inference/tools.py:18105-18107),
   so every truncated page is `cap + len(note)` long. Live evidence: 16036 for a 16000 cap, 10036 for a
   10000 share, 4036 for a 4000 share (the note is 36-37 chars). The route docstring and the MCP tool
   description both state exact ceilings ("5 get 4000 each", "at most 16000"), so a client that budgets
   on the stated number is short by up to ~36 chars per URL (about 180 for a 5-URL call: 20,180 vs the
   20,000 total). Not fixed, per the task constraints. A route-side trim to `cap` after the engine
   returns, or a cap reduced by the note length, would make the promise exact.
2. **Minor, pre-existing: SIGINT shutdown warning.** `Task was destroyed but it is pending!` for
   `sse_starlette.sse._shutdown_watcher` on Ctrl+C. It is not caused by this change (nothing in
   `routes/web.py` or the MCP tool uses SSE), but it shows up in the dev log and someone may read it as
   a regression from the new route.
3. **Not a bug, recorded because the plan expected it:** the MCP text block is one JSON document
   (`{"pages": [...]}`), not concatenated per-page prose. A Hermes client sees the whole payload twice
   (text block plus structuredContent), by design for the dual-emit path.

## Quality observations (engine, not this change)

- Wikipedia extraction keeps the language-link list at the top of the returned Markdown
  (`* [Español] ... * [Українська]`), which eats the first ~700 chars of a budgeted page. That is
  `_html_to_md`'s main-content heuristic on this page, unchanged by this work.
- sbox.game returning "no readable text" is the expected JavaScript-site behaviour and matched
  prediction. There is no retry path (by design, per the tool description).

## What was not tested

- Nothing was loaded into a model and no GPU work was done, so `_dense_char_limit`'s model-loaded
  shrink path was not exercised (no model in the dev instance).
- No Hermes-side wiring, no `/reload-mcp`, no tunnel, no LAN bind.
