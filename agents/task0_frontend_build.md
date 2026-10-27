# Task 0 — Frontend build (`studio/frontend/dist`)

**Status: done.** `npm run build` succeeded end-to-end (typecheck + vite). No fallback to
`npx vite build` was needed.

## Method (mirrored from `studio/setup.sh`)

`studio/setup.sh` `_build_frontend()` does, in `studio/frontend`:

- install: `npm ci --no-fund --no-audit --loglevel=error` when `package-lock.json` exists
  (bun path only when there is no lockfile — not applicable here; a lockfile is present)
- build: `npm run build`

No extra env vars were required (`UNSLOTH_NPM_REGISTRY` / mirror vars are opt-in escape hatches;
the default registry is pinned in `studio/frontend/.npmrc`, which also enforces
`min-release-age=7`, `save-exact=true`, `allow-git=none`).

Commands actually run:

```
cd studio/frontend
npm ci --no-fund --no-audit --loglevel=error   # 1061 packages, 20.0 s
npm run build                                  # exit 0, 7.35 s wall
```

`package.json` has no `packageManager` field; `engines.node` is `^20.19.0 || >=22.12.0`.
The Node on PATH was **v26.7.0** with npm **11.19.0** (not the v22.22.1 in the task brief) —
it satisfies `engines` and npm >= 11.10 is required by the `.npmrc` supply-chain gate, so both
were fine.

## Result

- dist path: `/home/cseliot/1-Development/2-Variety/unsloth-eliot/studio/frontend/dist`
- size: **43 MB** (`du -sh`), **859 files**
- build time: **7.35 s** total (tsc typecheck + `vite build`); vite alone reports `✓ built in 2.99s`
- typecheck: passed (`typecheck: tsc (TypeScript 7.0.2)`) — no pre-existing errors, no fallback needed
- `dist/index.html` exists; every `/assets/*` reference in it resolves to a real file (verified)
- backend resolution matches: `studio/backend/run.py` `_DEFAULT_FRONTEND_PATH = <repo>/studio/frontend/dist`

Warnings (non-blocking, pre-existing):

1. Chunks > 500 kB after minification (`chat-*.js` 2.63 MB, `pdfjs` 1.60 MB, several others).
   Vite's default `chunkSizeWarningLimit` advice — informational only.
2. `vite.config.ts` uses features unsupported by `configLoader: 'native'`: `__dirname`
   (line 86) and extensionless imports of `./vite-plugin-child-selectors` /
   `./vite-plugin-viewport-media`. Only matters when Vite makes `native` the default loader.
3. `[DEP0205] DeprecationWarning: module.register() is deprecated` (plugin dep, node 26).

## Constraints honoured

- `git status --short` before == after (same 8 modified + 6 untracked paths, all pre-existing on
  branch `hermes-web-tools`). No tracked file touched.
- `dist/` and `node_modules/` are both covered by `studio/frontend/.gitignore` (verified with
  `git check-ignore -v`), so the build output adds nothing to the work tree.
- No global installs, no sudo, no `rm`. Nothing sent to the Studio on port 8888; `~/.unsloth` untouched.

## Caveat

The running Studio on :8888 was not restarted or queried, so whether *it* serves this bundle depends
on its own resolution order (frozen/site-packages dist vs repo dist). It resolves to the repo dist
only when it runs from this checkout with no packaged frontend ahead of it.
