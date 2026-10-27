# Exposing web extract (and later browser use) from Unsloth Studio to Hermes over MCP

Scope: read-only research on this clone (branch main, HEAD 83c1c6256). No server was started, no
request was sent to localhost:8888, no credential file was read.

## Summary

1. Studio's MCP server is a FastMCP app mounted at `/mcp` behind `StudioMcpGate`, which requires an
   `sk-unsloth-...` API key and records a `Caller`; each tool then calls Studio's own HTTP routes
   through `studio_mcp/forward.py` as that key (mcp_server.py:45, gate.py:124, forward.py:57).
2. A new tool is a new module under `studio_mcp/tools/` with a `TOOLS` tuple, imported in
   `mcp_server.py:11-24` and listed at `mcp_server.py:49-62`; no per-tool policy or setting exists,
   so a web tool needs none.
3. Do not reuse `/api/browser/fetch`: it returns raw HTML for the sandboxed frame
   (routes/browser.py:2094-2102). Reuse the extraction engine behind Studio's own web tool
   (`web_search` with a `url` argument, tools.py:18640-18647),
   `core/inference/tools.py:_fetch_page_text` (tools.py:18111), which already has SSRF pinning, size
   caps, charset and encoding handling, PDF text, GitHub shortcuts and HTML to Markdown conversion.
4. Browser tools never reach an MCP caller: they are offered only when the request opts into UI
   stream events (routes/inference.py:6742, 32817) and they need a live chat tab to execute an
   action (browser_agent.py:702-724, state/browser_actions.py:4-11). Browser use is a separate
   project; options and costs are in section 4.
5. A dev instance can run from this clone on 8889 with its own `UNSLOTH_STUDIO_HOME`, `--api-only`
   (no frontend build), `UNSLOTH_STUDIO_ENABLE_MCP=1`, and a key minted offline with
   `auth.storage.create_api_key`; the live app and `~/.unsloth/studio` stay untouched (section 5).

## 1. How MCP tools are registered, authenticated and gated

Registration
- `create_studio_mcp()` builds one `FastMCP("Unsloth Studio", instructions = INSTRUCTIONS)`
  (mcp_server.py:45-47), imports twelve tool modules (mcp_server.py:11-24) and loops
  `for tool, annotations, *schema in module.TOOLS: mcp.tool(tool, annotations = annotations, ...
  output_schema = schema[0])` (mcp_server.py:63-66). The function name becomes the tool name, the
  docstring becomes the description, and the `Annotated[..., Field(...)]` signature becomes the input
  schema (see `embed`, studio_mcp/tools/text.py:196-220).
- Prompt templates are registered from `studio_mcp/prompts.py` (mcp_server.py:68-69).
- Mounted unconditionally in main.py: `create_studio_mcp().http_app(path = "/", stateless_http =
  True)` then `app.mount("/mcp", StudioMcpGate(_studio_mcp_app))` (main.py:1205-1209).

Authentication and gating (`studio_mcp/gate.py`)
- Off switch: while the owner's switch is off every `/mcp` request gets 404, decided before anything
  about the caller is read (gate.py:142-144). The switch is `is_mcp_enabled()` from
  `utils/mcp_access.py`: setting `studio_mcp_enabled`, read as the owner, fails closed, and
  `UNSLOTH_STUDIO_ENABLE_MCP=1` forces it on (utils/mcp_access.py:16, 37-63). The UI route is
  `/api/settings/mcp-access` and only the UI may change it (tests/test_mcp_access_route.py:18).
- Origin: a request carrying a foreign `Origin` is refused 403 (gate.py:146-158). Command line agents
  send no Origin.
- Key: exactly one `Authorization: Bearer`, scheme must be `bearer`, the token must start with
  `sk-unsloth-`, dummy/keyless bearers are refused, the retired `UNSLOTH_STUDIO_MCP_TOKEN` is refused
  (gate.py:160-183, 22-33), and internal workflow keys (recipe, Deep Research) are refused because
  they carry narrower authority than every tool (gate.py:69-82).
- Request body is capped at 4 MiB (gate.py:36, 193-196). This caps inputs only; nothing caps the
  size of a tool result.
- The validated key is turned into `Caller(token, account_id, direct_local, public_base, studio_app,
  hf_token)` and stored in the ASGI scope state (gate.py:204-213); tools read it back with
  `current_caller()` (caller.py:32-38). `direct_local` is owner role plus a direct loopback
  connection, and it gates host file paths only.
- There is no per-tool permission list and no per-tool setting. `state/tool_policy.py` governs the
  chat tool loop (`--enable-tools`, per-request `enable_tools`), not MCP tools; MCP `chat` actually
  forces tools off for its forwarded request (`"enable_tools": False`, studio_mcp/tools/text.py:136,
  inside `with tools_force_disabled():`, text.py:148), which is why Studio's own web tool
  (`web_search`, including its `url` fetch mode) never runs inside an MCP chat.

How a tool does work
- Every tool forwards to Studio's own routes in process, as the caller's key: `forward()` builds an
  `httpx.AsyncClient` over `ASGITransport(app = caller.studio_app)` with a TEST-NET peer and an
  `.invalid` Host, so no loopback or keyless shortcut can apply (forward.py:4-20, 57-94).
- Paths are allowlisted by prefix in `ALLOWED_PREFIXES` and checked by `checked_path()`, which also
  rejects `.` and `..` segments (forward.py:24-45). A new web route must be added there.
- Route failures are turned into `ToolError` with an actionable message and scrubbed of host paths by
  `raise_for_route` (studio_mcp/errors.py:1-6); `route_json()` is the thin wrapper most tools use
  (studio_mcp/tools/__init__.py:24-38).

Exact place to add `web_extract`
- New file `studio/backend/studio_mcp/tools/web.py`, SPDX header, a `TOOLS` tuple at the bottom, for
  example `TOOLS = ((web_extract, READ_ONLY_WEB, WebExtractResult.model_json_schema()),)`.
- `mcp_server.py`: add `web` to the import block (11-24) and to the module tuple (49-62).
- Annotations: `READ_ONLY` is `readOnlyHint = True, openWorldHint = False` and its comment says every
  tool acts on this Studio only (studio_mcp/tools/__init__.py:17-20). A web tool reaches the open
  internet, so it needs its own `ToolAnnotations(readOnlyHint = True, openWorldHint = True)` rather
  than reusing `READ_ONLY`.
- `tests/test_mcp_server.py:10-38` asserts the exact tool set (23 names, split 8 read-only, 3
  destructive, 12 other) and the annotations of each, so it must be updated in the same commit.
- Optional: one line in `INSTRUCTIONS` (mcp_server.py:26-42) and a table row plus a short section in
  `studio/MCP.md` (tools table from line 273, "Unsloth Studio's own MCP server" from line 78).

## 2. What fetch and extraction code to reuse

`/api/browser/fetch` is not an extractor
- `BrowserFetchRequest` takes url/method/body/max_bytes/error_page (routes/browser.py:1614-1622);
  `_fetch` calls `_fetch_url_raw` with a 50 MB body cap, a browser User-Agent and
  `proxy_hostname = True` (routes/browser.py:57-58, 2019-2040); `_build_response` returns
  `{"url", "base", "ownBase", "anyOrigin", "refresh", "html"}` for HTML (routes/browser.py:2094-2102)
  because the panel renders it in an opaque-origin iframe. No text conversion, no Markdown, no PDF
  text. Wrong tool for this job.

The engine to reuse: `core/inference/tools.py:_fetch_page_text` (tools.py:18111-18216)
- Normalizes a bare host to https (`_normalize_url_scheme`, tools.py:17031-17070), applies the
  website access policy (`check_url_access`, web_access_policy.py:131-150), rewrites a GitHub repo
  root to the README API (tools.py:16628-16648) and a GitHub blob link to raw.githubusercontent
  (tools.py:16651-16663), extracts PDF text (tools.py:16239-16275), and converts HTML to Markdown
  with `_html_to_md.html_to_markdown(..., main_content = True, site_links = ...)`
  (tools.py:18206-18212). Plain text, Markdown and JSON pass through untouched (tools.py:18199-18203).
  Output is one string, truncated with a `... (truncated, N chars total)` notice (tools.py:18102-18108).
- SSRF: `_validate_and_resolve_host` resolves once, rejects anything that is not `ip.is_global`
  (private, loopback, link-local, multicast, reserved, unspecified), and the fetch pins to the
  validated IPs (tools.py:16510-16551). HTTPS keeps SNI and cert verification against the hostname
  while connecting to the pinned IP (tools.py:16362-16425). Redirects are followed at most five
  times, each hop re-checked against the policy (tools.py:17150, 17223-17263, 17338-17342). A fake-IP
  DNS resolver (Clash/sing-box) is allowed only after a resolver probe proves it (tools.py:16472-16499).
  System proxies are handled explicitly so a proxied hop is not pinned wrongly (tools.py:16448-16469).
- Size caps and timeouts: 16,000 characters of page text (`_MAX_PAGE_CHARS`, tools.py:15953), 512 KiB
  decoded body, 8 MiB for HTML, 10 MiB for PDFs, 50 PDF pages (tools.py:15964-15972), 30 s default
  timeout with one wall-clock deadline covering every redirect and body read
  (tools.py:16669-16688, 18116, 18129).
- Encoding: `Accept-Encoding: gzip, deflate` with a bounded streaming inflater (tools.py:16881-16909,
  17209, 17265-17268); charset from the header, a BOM, or a WHATWG-labelled `<meta>` prescan, with a
  cp1252 rescue only when the bytes look like text (tools.py:17389-17431).
- Binary handling: MIME rejection for known binary application subtypes and magic-signature checks
  (tools.py:15979-15991, 16554-16587, 17369-17387), so a zip or image returns a short placeholder
  string instead of garbage.
- Extras that matter for an agent: `<meta http-equiv=refresh>` following (tools.py:16185-16236,
  17330-17340), bot-check detection (Cloudflare, DataDome, Akamai) turned into "fetching this site
  again will not help" (tools.py:17087-17102, 18190-18195), and a GitHub README that is already
  Markdown returned as is (tools.py:18151-18166).
- It returns errors as strings, never raises: `Failed to fetch URL: ...`, `Blocked: ...`,
  `(non-text content: ...)`, `(binary content, ...)`, `(PDF ...)`, `(page returned no readable text)`.
  That is the one wrinkle a new tool has to handle (section 3).
- Studio's own `web_search` reaches this through its `url` argument, capped at 60 s:
  `_web_search(..., url = ...)` calls `_fetch_page_text` and returns it verbatim
  (tools.py:18628-18647). This is the same path Hermes wants to replace, already tested by
  tests/test_web_fetch_extraction.py and the five sibling test files.

Recommendation: call `_fetch_page_text` unchanged, from a new authenticated route, and let the MCP
tool forward to that route. Reasons: it keeps the contract in forward.py:4-6 (tools reach Studio as
the caller, so auth, account scoping and redaction are not bypassed), it keeps the heavy import and
the blocking socket work out of the MCP tool module, it is directly testable with
`fastapi.testclient`, and it leaves `core/inference/tools.py` untouched, which matters for merge
conflicts (section 7). The fallback, if reviewers reject a new route, is to import `_fetch_page_text`
inside the tool and run it with `asyncio.to_thread`; that is fewer files but breaks the documented
"every tool goes through a route" rule.

## 3. Design for the `web_extract` MCP tool

Name: `web_extract`. One tool, no `web_search` companion in this pass (Studio has search code, but
the ask is extract only, and Hermes has search already).

Input schema, kept minimal:

```python
async def web_extract(
    urls: Annotated[
        list[str],
        Field(
            min_length = 1,
            max_length = MAX_EXTRACT_URLS,
            description = "http(s) URLs to read, in order. At most 5.",
        ),
    ],
    max_chars: Annotated[
        Optional[int],
        Field(
            ge = MIN_EXTRACT_CHARS,
            le = MAX_EXTRACT_CHARS,
            description = "Characters per page (default 8000, at most 16000).",
        ),
    ] = None,
) -> WebExtractResult:
```

- No `format` parameter: `_fetch_page_text` already returns Markdown for HTML and the raw text for
  anything else, which is what an agent wants. Nothing to choose.
- No `timeout` parameter: fixed 30 s per URL plus a 60 s budget for the whole call, so five slow
  sites cannot hold a tool call for 150 s.
- `max_chars` defaults to 8000, below the engine's 16,000, because up to five pages come back in one
  result and agent context is the scarce resource.

Route: `POST /api/web/extract` (new `studio/backend/routes/web.py`, or a clearly separated block in
routes/browser.py if a new router is unwelcome). Body `{"urls": [...], "max_chars": 8000}`.
Handler: validate count and length with Pydantic (`max_length = 8192` per URL, like
BrowserFetchRequest), then for each URL `await asyncio.to_thread(_fetch_page_text, url,
max_chars = max_chars, timeout = 30)` because the fetcher is blocking; run at most 3 at a time with
`asyncio.Semaphore` so one caller cannot open 5 sockets per call across many calls. Response:

```json
{"results": [{"url": "https://example.com/a", "text": "# Title\n\n...", "error": null}]}
```

Errors are per URL, never a 500: the handler maps the engine's string prefixes
(`Failed to fetch URL: `, `Blocked: `, `(non-text content`, `(binary content`, `(PDF `,
`(page returned no readable text`) to `error` with the same wording and an empty `text`. A 200 for a
page that failed keeps the MCP result usable and matches Hermes' per-entry error shape
(`plugins/web/_common.py:62-67`).

Output model, in `studio_mcp/outputs.py` next to the others:

```python
class WebPage(ToolOutput):
    url: str
    text: str = ""
    error: Optional[str] = None

class WebExtractResult(ToolOutput):
    pages: list[WebPage] = []
```

The tool forwards with `route_json("POST", "/api/web/extract", json_body = {...})` and maps
`results` into `pages`, so a route-level failure (400 for too many URLs) still becomes a `ToolError`
with the route's message. `forward.py:24-37` needs `"/api/web/"` added to `ALLOWED_PREFIXES`.

Limits and gates
- Max 5 URLs, 8192 characters of URL, 8000 default / 16000 max characters per page. The 4 MiB
  request cap in gate.py is irrelevant at these sizes.
- No new setting and no new gate. Availability is the existing `studio_mcp_enabled` switch plus a
  valid key; per-account policy is whatever the key may do, and today `check_url_access` runs with no
  policy (empty allow/block lists), so every public http(s) host is reachable, exactly as Studio's
  own web tool behaves.
- Deliberate choice: do not consult `state/tool_policy.py`. That policy is a per-request chat option
  and has no per-request meaning for an MCP call. If consistency with `--disable-tools` is wanted, add
  one check of `get_tool_policy() is False` at the top and raise `ToolError`; say so in the commit.
- No `title` field in v1. `_fetch_page_text` does not return the final URL or the `<title>`, and
  recovering them means either a second fetch or editing tools.py. Agents need the text and the URL
  they asked for; Hermes' own `document()` shape includes a title, so if that matters the MCP-side
  adapter in Hermes can use the URL as the title. Adding `final_url`/`title` later is a small,
  additive change to the route only.

Mapping to what Hermes' `web_extract` returns (`{"results": [{"url", "title", "content",
"raw_content", "metadata"}]}`, plugins/web/_common.py:54-59): the MCP tool returns the same
information minus the title, and Hermes' side is a thin adapter either way, since the tool call is an
MCP call, not a web backend plugin.

## 4. Browser use

Why an MCP caller never sees the browser tools
- They are added to the request's tool list only when `tools_on and ui_events and
  payload.enabled_tools is not None`, and the screenshot variant only for a vision model
  (routes/inference.py:6742-6752). The comment states the reason: only a chat tab with the browser
  panel open can run them.
- `ui_events` is true only when the request carries `X-Unsloth-Events: 1`
  (routes/inference.py:32814, 32817-32832). `forward()` sends only `Authorization`,
  `X-Unsloth-HF-Token` and `Content-Type` (forward.py:48-54), so an MCP-forwarded chat is always
  `ui_events = False`. MCP `chat` also sends `enable_tools: False` (studio_mcp/tools/text.py:136)
  inside `tools_force_disabled()` (text.py:148), so even a caller that did set the header would get no
  Studio tools.
- Even if a browser tool were offered, `execute_browser_tool` refuses without an `output_callback`
  ("the browser is only available in an Unsloth chat", browser_agent.py:720-724). With one, it
  registers a slot and blocks until the tab posts to `/api/inference/browser-action-result`
  (browser_agent.py:737-769, state/browser_actions.py:46-66, routes/inference.py:23539-23550), and
  the answer is only accepted from the same session and account that asked
  (state/browser_actions.py:69-82). The browser itself lives in the tab: a proxied iframe
  (`/api/browser/frame`, routes/browser.py:2465) or a desktop native view.
- There is no headless browser anywhere in the tree: no playwright, selenium, pyppeteer or webdriver
  dependency in `studio/` or the requirements files, and the desktop shell is Tauri 2 with
  webkit2gtk on Linux (studio/src-tauri/Cargo.toml:19, Cargo.lock entry `webkit2gtk`), which has no
  CDP endpoint.

Options for later, with cost and risk. Nothing to build now.

(a) Studio window as the executor
- Idea: the MCP tool registers a browser action and the open UI executes it, the way a chat does.
- What is missing: a channel for a non-chat caller to reach the tab. Today the only carrier is the
  chat SSE stream of the request that asked (`tool_output` event with `browser_action`), and the tab
  only listens on its own chat stream. A new subscription (an SSE or WebSocket endpoint the browser
  panel listens on, plus UI work to accept and run actions from it) is required, in Python and in
  React, plus consent UX because an agent would be driving a page the user is looking at, plus
  session and account binding of the actions.
- Cost: 2 to 3 days across backend and frontend. Risk: medium. It is the only option that browses
  with the user's real cookies and stays inside the existing SSRF, policy and screenshot code, but it
  only works while a Studio window is open and watching, and it makes the agent's actions visible and
  disruptive.

(b) Headless Playwright/Chromium in the backend
- Idea: agent-owned browser process, driven over CDP, exposed as `browser_navigate`, `browser_click`,
  and so on.
- Cost: new dependency and a browser download in the installer (roughly 300 to 400 MB) on three
  platforms, a lifecycle manager (one browser, one context per account, idle shutdown), navigation
  SSRF checks (the rules in browser_agent.py:392-430, `_host_problem`, `_is_loopback`,
  `_studio_port_problem`, are reusable as a starting point), download and upload handling, timeouts,
  and packaging plus CI changes, since no browser automation exists in the tree today.
- Effort: 4 to 6 days for something usable. Risk: high for packaging and support, low for semantics
  (an agent-owned browser is what an agent wants); it also adds a large binary to a desktop app that
  currently ships none.

(c) Other options worth knowing
- Let the agent bring its own browser MCP server: Studio already supports registering external MCP
  servers (routes/mcp_servers.py, `studio_mcp` is the reverse direction), and Hermes can add a
  Playwright or Chrome DevTools MCP server itself. Zero Studio code, zero new dependencies in Studio,
  and the user's existing browser profile can be reused. Cost: minutes on the Hermes side; risk: it
  lives outside Studio, so it is not part of the Studio-to-agent story.
- Drive the Tauri WebView: no CDP on WebKitGTK, so this means OS-level automation (AT-SPI, xdotool),
  which is the `computer-use` style path: fragile, unavailable headless, and it would fight the user
  for the pointer. Not recommended.
- Reuse `/api/browser/request` or the frame shell: those are rendering helpers for the iframe. The
  server never holds a DOM, so there is nothing to query.

Recommendation: ship `web_extract` first, and when browser use is wanted, start with (c) for the
agent's own browsing and consider (a) only if the requirement is specifically "drive the Studio
window the user is watching". (b) is the best long term and the most expensive.

## 5. Dev backend on port 8889, without touching `~/.unsloth/studio`

Facts used
- Studio's root is `UNSLOTH_STUDIO_HOME`, then `STUDIO_HOME`, then `UNSLOTH_HOME/studio`, then a venv
  inference, then `~/.unsloth/studio` (utils/paths/storage_roots.py:231-255). `run.py` and `main.py`
  only set it when it is unset (run.py:1363-1364, main.py:306-307), so exporting it is enough.
- The auth database is `auth_db_path()` under that root (auth/storage.py:25-29), so a separate home
  means a separate `auth.db`, `studio.db`, logs and downloads. Nothing under `~/.unsloth/studio` is
  read or written.
- `--port` exists (run.py:3209) and `--api-only` exists (run.py:3218-3221); with `api_only` the
  frontend is not served (`_frontend_serving_mode`, run.py:1791-1793) and not required to exist
  (run.py:2728-2736), and the default `studio/frontend/dist` does not exist in this clone. The MCP
  endpoint is mounted regardless of the frontend (main.py:1209), so no frontend build is needed.
- `is_mcp_enabled()` can be forced with `UNSLOTH_STUDIO_ENABLE_MCP=1`, which also locks the Settings
  switch (utils/mcp_access.py:16, 33-34, 62-63).
- The owner account is seeded at startup (`storage.ensure_default_admin()` called from main.py:1050),
  and API keys are validated with a JOIN against `auth_user`, so the account must exist before a key
  is minted (auth/storage.py:1405-1425, 2004-2017). Creating a key on a fresh install is normally
  blocked over HTTP by `must_change_password`; the CLI works around it with a direct storage call
  (unsloth_cli/commands/studio.py:896-916).

Steps
1. Create the branch and the dev home. Do not reuse the live home.
   ```bash
   cd /home/cseliot/1-Development/2-Variety/unsloth-eliot
   git switch -c feat/mcp-web-extract
   export UNSLOTH_STUDIO_HOME=/home/cseliot/dev/studio-8889   # any path outside ~/.unsloth/studio
   mkdir -p "$UNSLOTH_STUDIO_HOME"
   ```
2. Environment. Own venv, own deps, no change to the live one:
   ```bash
   python3 -m venv .venv-dev
   .venv-dev/bin/pip install -r studio/backend/requirements/base.txt \
                               -r studio/backend/requirements/studio.txt
   .venv-dev/bin/pip install -e .          # unsloth itself, as setup.sh does for a local install
   ```
   `studio.txt` pins fastapi, uvicorn, fastmcp and the rest (studio/backend/requirements/studio.txt:6,
   :51). The torch half of the stack is only needed because `main.py` imports the inference layer, so
   a full install is large; if that is too slow, run the first smoke test with the existing installer
   venv (`~/.unsloth/studio/unsloth_studio/bin/python studio/backend/run.py ...`) purely as an
   interpreter. Reading that venv is harmless; the state still goes to `$UNSLOTH_STUDIO_HOME` because
   the env var outranks the venv inference (utils/paths/storage_roots.py:238-255).
3. Start it:
   ```bash
   UNSLOTH_STUDIO_ENABLE_MCP=1 UNSLOTH_STUDIO_HOME=/home/cseliot/dev/studio-8889 \
     .venv-dev/bin/python studio/backend/run.py --port 8889 --api-only
   ```
   Expect a slow first import: `run.py` says `from main import app` loads torch, unsloth and
   transformers and can take minutes cold (run.py:2651-2657). Do not run a second instance against
   the same home (`abort_if_own_studio`, run.py:2711).
4. Mint an API key in that home, after the first start has seeded the owner:
   ```bash
   UNSLOTH_STUDIO_HOME=/home/cseliot/dev/studio-8889 .venv-dev/bin/python - <<'PY'
   import sys
   sys.path.insert(0, "studio/backend")
   from auth import storage
   raw, _row = storage.create_api_key(
       username = storage.DEFAULT_ADMIN_USERNAME, name = "dev-8889"
   )
   print(raw)
   PY
   ```
   Keys are read from the database per request, so no restart is needed. Do not write the key to a
   tracked file. `unsloth doctor --mcp` can also check the connection, but it targets the default port
   unless told otherwise.
5. Point Hermes at the dev instance:
   ```yaml
   mcp_servers:
     unsloth_studio_8889:
       url: "http://127.0.0.1:8889/mcp/"
       headers:
         Authorization: "Bearer ${UNSLOTH_API_KEY_8889}"
   ```
   (the same shape studio/MCP.md:216-224 documents for 8888), then `/reload-mcp`.
6. Extraction testing needs no model and no GPU: `web_extract` touches only the network. Keep it that
   way on 8889 so the dev instance never competes with the live app for VRAM.
7. Sanity check that the live app is untouched: 8889 answers, 8888 still answers, and
   `~/.unsloth/studio` mtimes are unchanged.

Note: with `--api-only` the process sets `UNSLOTH_API_ONLY=1` and CORS stays locked to the Tauri
origin (run.py:2626-2631). curl and Python clients send no Origin, so they are unaffected, and the
MCP gate only rejects a foreign Origin (gate.py:146-158).

## 6. Test plan

Files to add or extend
- `tests/test_mcp_tools_web.py` (new): mirror `tests/test_mcp_tools_status.py` and
  `tests/test_mcp_tools_text.py`. Use `tests/mcp_harness.py`: `run_tool(monkeypatch, fake, "web_extract",
  args)` drives the real gate, caller and forwarder against a fake Studio (`fake_studio`, `run_tool`
  at mcp_harness.py:93-120 and 210-225). Cover: the forwarded method, path and JSON body; two URLs
  returned in order; a per-page error preserved; refusal for an empty list and for 6 URLs (the fake
  Studio must see no call, mirroring the `REFUSED` rows in tests/test_mcp_tool_contract.py:48-80); a
  4xx from the route surfacing as `ToolError` with the route message.
- `tests/test_mcp_server.py`: add `web_extract` to the read-only set (lines 10-38 assert the exact
  set and each tool's annotations), and assert `openWorldHint is True` for it in the per-tool loop.
- `tests/test_mcp_tool_contract.py`: add the web rows for refused inputs.
- `tests/test_web_extract_route.py` (new): mirror `tests/test_browser_routes.py` for the route. Cover
  auth required (401 without a subject), a policy-blocked URL reported as an error entry, a non-text
  content type reported as an error entry, and that the handler runs the fetcher off the event loop
  (thread id assertion, the pattern in tests/test_mcp_tool_read_off_event_loop.py).
- `studio/MCP.md`: a table row and a short section. Docs drift is a review comment otherwise.
- Do not extend `tests/test_web_fetch_extraction.py` unless `_fetch_page_text` itself changes. It is
  the engine's coverage and it must stay green as a regression signal.

Commands (CI shape, from `studio/backend`)
```bash
python -m pytest tests/test_mcp_tools_web.py tests/test_mcp_server.py \
  tests/test_mcp_tool_contract.py tests/test_web_extract_route.py \
  -q --tb=short -n 4 --dist loadgroup --timeout=330
```
and, because it is deliberately excluded from the xdist run and executed serially
(.github/workflows/studio-backend-ci.yml:280, 293-309):
```bash
python -m pytest -q --tb=short --timeout=330 tests/test_web_fetch_extraction.py
```
No network in any of these: the extraction fixtures are offline snapshots
(tests/test_web_fetch_extraction.py:4-5), and the MCP tests use the fake Studio.

## 7. Effort, risks, upstream conflict risk

Effort
- `web_extract`: 1 to 1.5 days. New route about 60 lines plus tests, tool module about 100 lines,
  output models about 10, three lines in `mcp_server.py`, one line in `forward.py`, docs. Review and
  one round of fixes included.
- Browser option (a): 2 to 3 days, backend plus React. Option (b): 4 to 6 days plus packaging. Option
  (c): no Studio work.

Risks
- Error classification by string prefix. `_fetch_page_text` signals failure in the returned string,
  so the route must match prefixes. It is a handful of stable prefixes (tools.py:17227, 17375, 17384,
  18104, 18192, 18195) but a wording change upstream would silently turn an error into "content".
  Mitigation: assert each prefix in the route test, so an upstream reword fails a test instead of
  shipping.
- Empty extractions on JavaScript-only sites. `_fetch_page_text` has no JS, so a SPA returns
  `(page returned no readable text)` (tools.py:18104). For an agent that currently fails outright,
  this is still progress, but it is the main reason browser use is wanted later. Say so in the tool
  description so the agent does not retry five times.
- Latency. 30 s per URL with a 60 s total budget keeps a call bounded, but Hermes' MCP client tool
  timeout must be above that. Mention it in the docs next to the existing 240 s advice
  (studio/MCP.md:193-198).
- Output size. Nothing caps a tool result (gate.py's 4 MiB is the request body), so the 8000 default
  and the 5 URL cap are the only guard against flooding the agent's context.
- Proxy and fake-IP behavior differ from `/api/browser/fetch` (`proxy_hostname = True` there,
  routes/browser.py:2038). Extraction inherits the direct-fetch pinning, which is stricter. That is
  intended; note it for anyone comparing the two.
- Do not duplicate the SSRF code. The pinning, SNI and fake-IP logic is subtle and tested; a second
  copy in a web route would drift.

Upstream conflict risk
- This fork tracks `unslothai/unsloth` (only `origin` is configured here, and the fork's main moves
  upstream daily; HEAD is dated 2026-10-25). 30-day commit counts in this clone:
  `core/inference/tools.py` 70, `main.py` 62, `routes/browser.py` 23, `mcp_server.py` 4,
  `studio_mcp/forward.py` 3.
- So the design keeps all logic in new files (`routes/web.py`, `studio_mcp/tools/web.py`,
  `tests/test_mcp_tools_web.py`, `tests/test_web_extract_route.py`) and touches the three churn-free
  spots with one-line additions: the import and module tuple in `mcp_server.py`, the prefix tuple in
  `forward.py`, the tool set in `tests/test_mcp_server.py`. Expected conflict: none, and at worst a
  trivial tuple edit.
- Explicitly avoid editing `core/inference/tools.py` and `routes/browser.py`, the two hottest files.
  That is the main reason the design reuses `_fetch_page_text` as is instead of refactoring it into a
  shared helper.

## UNVERIFIED

- How Hermes' own MCP client renders a tool that declares `output_schema`/`structuredContent`, and
  whether it prefers that over the text content. I read only Hermes' `web_extract` contract
  (`tools/web_tools.py:392`, `plugins/web/_common.py:54-67`).
- Why Hermes lists 22 Studio tools while `tests/test_mcp_server.py` asserts 23 names. Likely a
  client-side filter or an older cached tool list; not inspected.
- Whether `python3 -m venv` plus `requirements/base.txt` and `requirements/studio.txt` is enough to
  import `main.py` and serve `/mcp` without the torch half of the stack. Not installed, not run.
- The cold-start time of `run.py` on this machine (its own comment claims about two minutes).
- The exact value `_dense_char_limit` / `_exact_prefix_chars` return when no model is loaded and
  `max_chars` is passed explicitly. Not executed; the design passes an explicit cap to stay
  independent of it.
- Whether `unsloth doctor --mcp` accepts a non-default port for the dev instance.
- That Tauri's WebKitGTK webview has no CDP endpoint. Inferred from the Tauri 2 plus webkit2gtk
  dependency; not tested on this machine.
