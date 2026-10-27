# Review: task0_research.md (web_extract MCP tool for Unsloth Studio)

Verdict: APPROVE WITH FIXES. The architecture reading is accurate: gate, forwarder, TOOLS
registration, the `_fetch_page_text` recommendation, the browser-tool gating, and the dev-home
plan all check out against the code. The errors are a handful of citation drifts, one
test-assertion the plan would fail, one overstated "the dev instance touches nothing" claim,
and one missing wiring step (`main.py`).

## Wrong claims

1. "Hermes lists 22 Studio tools while `tests/test_mcp_server.py` asserts 23 names"
   WRONG, and the premise is a miscount. `test_mcp_server.py` asserts 23 (8 read-only, 3
   destructive, 12 other). Hermes' own registry for this profile exposes all 23
   (`analyze_audio`, `cancel`, `chat`, `datasets`, `download_model`, `embed`, `export_model`,
   `generate_audio`, `generate_image`, `generate_video`, `get_job`, `list_media`,
   `list_models`, `list_training_runs`, `load_model`, `plan_load`, `run_recipe`,
   `start_training`, `studio_status`, `system_one`, `transcribe`, `unload_model`, `voices`)
   plus 4 prompt/resource helpers (`list_prompts`, `get_prompt`, `list_resources`,
   `read_resource`) = 27 entries. Nothing is filtered or colliding.

2. "add `web_extract` to the read-only set ... and assert `openWorldHint is True` for it in the
   per-tool loop" (test plan, `tests/test_mcp_server.py`)
   WRONG as written, and a first pass following it fails. Line 52 asserts
   `hints.openWorldHint is False` for EVERY tool, unconditionally. Adding a member to
   `READ_ONLY` and a True assertion alongside does not fix line 52; it has to become
   conditional (e.g. `assert hints.openWorldHint is (name in OPEN_WORLD)`). Line 53 also pins
   `readOnlyHint is (name in READ_ONLY)` and line 56 pins `destructiveHint` for non-read-only
   tools, so the new name must go in `READ_ONLY` for those to hold.

3. "Do not run a second instance against the same home (`abort_if_own_studio`, run.py:2711)"
   WRONG on both counts. run.py:2710-2711 is `if abort_if_own_studio is None: abort_if_own_studio
   = not api_only`, so `--api-only` (the plan's own flag) turns the guard off; and it guards the
   port via `_resolve_port(..., avoid_own_studio = ...)`, not the home. Two instances pointed at
   one home are not refused by this code, so the plan needs a manual "check the home is empty
   and nothing else is using it" step.

4. "Nothing under `~/.unsloth/studio` is read or written" (section 5)
   OVERSTATED. Writes: none found for a fresh `UNSLOTH_STUDIO_HOME`. Reads: the legacy fallbacks
   still look there and at `~/.unsloth/llama.cpp` (core/inference/llama_cpp.py:10774 and 38299,
   audio_cpp_server.py:99-107, sd_cpp_engine.py:585-589, stt_ggml_sidecar.py:133-142). Worse,
   step 2's shortcut of running the installer venv as the interpreter is not read-only: startup
   calls `_repair_pinned_diffusers` before importing main (run.py:2475-2488), and
   `repair_diffusers_before_imports` pip-installs with `sys.executable`
   (utils/diffusers_repair.py:239); both marker files exist here
   (`studio/install_python_stack.py`, `studio/backend/requirements/diffusers-main.txt`), as does
   the kernel lock dir under `sys.prefix` (utils/kernel_install.py:256). Use the fresh venv, or
   export `UNSLOTH_DISABLE_DIFFUSERS_AUTOREPAIR=1`.

5. "tests/test_web_fetch_extraction.py and the five sibling test files"
   WRONG count: six siblings exist (test_web_fetch_binary_guard, _bot_check,
   _content_encoding, _fake_ip_dns, _meta_refresh, _scheme_normalization).

6. "`UNSLOTH_STUDIO_ENABLE_MCP=1` ... also locks the Settings switch (utils/mcp_access.py:16,
   33-34, 62-63)"
   Claim true, citation wrong. `utils/mcp_access.py` only defines `forced_by_env()`; the 409 that
   locks the switch is in routes/settings.py:4663-4664.

7. "passes an explicit cap to stay independent of it" (`max_chars` vs `_dense_char_limit`)
   HALF WRONG. `_fetch_page_text` always funnels through `_truncate_page_text`, which calls
   `_dense_char_limit(text, max_chars)` (tools.py:18105). With no model loaded that returns the
   cap unchanged (tools.py:18044-18049, and `_exact_prefix_chars` returns `chars` at
   17949-17953), but on the same Studio with a model loaded the page can come back shorter than
   `max_chars`.

8. Minor citation drifts (substance right): `studio/src-tauri/Cargo.toml:19` is the `tauri`
   dependency, `webkit2gtk` is at Cargo.toml:88-89; `tests/test_mcp_access_route.py:18` is the
   pytestmark, the UI-only rule is at :16 and :97-112; `studio/MCP.md` section heading is line
   72 (not 78) and the tools table header is line 276 (not 273); `auth/storage.py:1405-1425` is
   `ensure_default_admin`, the api-keys JOIN is 2004-2017 as also cited.

## Resolved unknowns

- Hermes MCP client vs `structuredContent`/`output_schema`: text blocks are primary.
  tools/mcp_tool_handlers.py:525-563 renders every text block, forwards `structuredContent`
  alongside unless its parsed JSON also appears verbatim in a text block (spec dual-emit), and
  lets it fill `result` only when the blocks rendered empty. So declaring an output schema is
  harmless and the model reads the text rendering. Hard cap is 2,000,000 chars
  (tools/mcp_tool_content.py:25), far above 5 x 8,000, so size is a context-budget question only.
- 22 vs 23: no discrepancy; see wrong claim 1.
- base.txt + studio.txt without torch: not achievable and the question is moot. base.txt is
  "Shared torch-bound ML requirements" (requirements/base.txt:1), so installing it installs
  torch; studio.txt alone is not enough because main.py imports the inference layer at import
  time (run.py:2651 says as much). A torch-free CI path exists
  (requirements/no-torch-runtime.txt) but was not exercised here.
- `_dense_char_limit` / `_exact_prefix_chars` with no model: both return the caller's cap
  unchanged (tools.py:17949-17953, 18044-18049). With a model loaded they can shrink it.
- `unsloth doctor --mcp` and a non-default port: no port flag exists
  (unsloth_cli/commands/doctor.py:519-543 -> `mcp_doctor(env_var)` -> `gather_mcp_facts` ->
  `find_studio_server`, unsloth_cli/_inference.py:813-825, which reads `UNSLOTH_STUDIO_URL`,
  default `http://127.0.0.1:8888`, plus recorded bases). To point it at 8889 set
  `UNSLOTH_STUDIO_URL`; it can also prefer a recorded base, so it is not a dependable 8889 check.
- Whether the tool shows in Studio's own UI lists: no frontend tool-name list exists for the
  Studio MCP server (grep for `studio_status`/`list_models` under `frontend/src` is empty), so
  no UI work and no UI drift.

## Design gaps

1. `main.py` wiring is missing from the plan. A new `routes/web.py` needs an import and an
   `app.include_router(...)` (browser's is main.py:2027, imports around 431/488). main.py is 62
   commits in 30 days in this clone, so "expected conflict: none" and "three churn-free spots"
   understate the merge risk; this is the one hot file the design cannot avoid.
2. `test_mcp_server.py:52` must be relaxed, not merely extended (wrong claim 2).
3. Concurrency: a per-call `asyncio.Semaphore(3)` bounds one call, not concurrent MCP calls, and
   `asyncio.to_thread` uses the default executor that inference shares. browser.py:64-65 keeps a
   dedicated pool precisely so a slow site cannot starve chat; this route needs the same (module
   level pool plus semaphore), not a per-request one.
4. Budget is per page, not per call: 5 x 8,000 chars is ~10k-20k tokens in one tool result. Add
   a total-chars budget (or make the default scale with URL count) rather than leaving the
   product of two caps as the only guard.
5. Provenance: no final URL and no title (the report knows), and a redirect chain or meta-refresh
   hop is invisible to the caller. Acceptable for v1, but say so in the tool description, because
   the agent will cite the URL it asked for.
6. No per-account rate limit or politeness on a new outbound-fetch surface reachable by a key in
   an agent loop. At minimum document it; a per-account counter is cheap.
7. Naming: Hermes' native tool is also `web_extract`. MCP tools are namespaced
   (`mcp__<server>__<tool>`) so nothing shadows, but the "replacement" story is a Hermes-side
   change and the same server will then offer a 24th tool; state that explicitly.
8. `INSTRUCTIONS` is ~1,650 chars against the `<= 1900` assertion in
   test_mcp_server.py:142 (measured from the source block: 1,823 bytes including quotes and
   indentation), so an added sentence has roughly 240 characters of room.

## Dev-instance safety

Confirmed: `studio_root()` honours `UNSLOTH_STUDIO_HOME` first (utils/paths/storage_roots.py:
231-253); run.py:1363-1368 and main.py:306-311 only set it when unset;
`auth_db_path()` is `<studio_root>/auth/auth.db` (storage_roots.py:315-320); with no
`UNSLOTH_HOME`/recorded master root, run.py:1369-1371 derives `UNSLOTH_LLAMA_CPP_PATH` from that
same home, so llama.cpp/whisper/audio.cpp land in the dev home, not `~/.unsloth`; the MCP app is
mounted regardless of the frontend (main.py:1209) and `studio/frontend/dist` does not exist, so
`--api-only` is required and sufficient.

Residual risks to fix in the plan:
- Do not "just use the installer venv as an interpreter". As above, startup can pip-install into
  `~/.unsloth/studio/unsloth_studio` via the diffusers repair, and kernel installs write
  `sys.prefix/.unsloth-kernel-locks`. Either always use `.venv-dev`, or set
  `UNSLOTH_DISABLE_DIFFUSERS_AUTOREPAIR=1`.
- Shared user caches outside the home: `~/.cache/unsloth` diffusion compile/prequant caches
  (core/inference/diffusion_compile_cache.py:89, diffusion_prequant_cache.py:68) and the HF cache.
  Harmless for web_extract, but they are not "inside `UNSLOTH_STUDIO_HOME`".
- No code-level guard against two processes sharing one home in `--api-only` mode; add a manual
  step to confirm the dev home is fresh and unused.
- Tauri is not involved at all; no Tauri state is touched. The live app keeps its own home and
  8888.

## Still unverified

- The WebKitGTK-has-no-CDP claim (inferred from the dependency, not tested).
- Cold-start time of `run.py` on this machine.
- Whether `.venv-dev` from base.txt + studio.txt resolves cleanly on this Python (nothing was
  installed).
- Exact `INSTRUCTIONS` length (approximated from the source block's byte count, not evaluated).
