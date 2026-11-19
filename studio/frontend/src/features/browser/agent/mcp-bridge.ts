// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

/** The desktop shell's side of the MCP browser bridge. While the window is open it long-polls
 *  /api/browser/actions/claim for browser actions an outside MCP client queued, and hands each one
 *  to the executor, which runs it in a browser tab and posts the answer back itself
 *  (POST /api/inference/browser-action-result). The claimed action is shaped like the chat stream's
 *  `browser_action`, so parseBrowserAction reads it unchanged. */

import { AUTH_SESSION_STORED_EVENT, authFetch, hasAuthToken, hasSettledAuthSession } from "@/features/auth";
import { isTauri } from "@/lib/api-base";
import { MCP_BRIDGE_CHAT } from "./bridge-chat";
import type { ActionContext } from "./executor";

const CLAIM_PATH = "/api/browser/actions/claim";
/** The server holds the request this long; it returns at once when something is already queued. */
const CLAIM_WAIT_S = 20;
/** A claim that never comes back (a stalled socket) would otherwise hold the loop for good. */
const CLAIM_TIMEOUT_MS = (CLAIM_WAIT_S + 15) * 1000;
/** A backend that answers at once with nothing pending must not turn the loop into a spin. */
const MIN_POLL_GAP_MS = 1_000;
const BACKOFF_FIRST_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
/** How often to look again while the app is signed out or still signing in. */
const AUTH_WAIT_MS = 5_000;
/** While the Browser tool is off, nothing is claimed: the flag is watched, and the loop only wakes
 *  when it changes or after this long, so a lost change cannot park the bridge for good. */
const DISABLED_WAIT_MS = 30_000;
/** How long the bridge's tab outlives its last action: long enough for the snapshot one `web_browse`
 *  reads after its navigation, short enough to leave no hidden view behind between calls. */
const RELEASE_IDLE_MS = 5_000;
const RESULT_PATH = "/api/inference/browser-action-result";

/** Names no site: a gate reads it for a host the user already named, and here the user named none. */
const CALLER = "An outside MCP client asked for this action; the user wrote no request for it here.";

type Bridge = { controller: AbortController; loop: Promise<void> };

// One bridge at a time, whatever mounts it: a second ensure either joins this one or is a no-op.
let bridge: Bridge | null = null;
// Errors worth one line each, not one per retry.
const noted = new Set<string>();
// Pending release of the bridge's tab after its last action, if one is scheduled.
let releaseTimer: ReturnType<typeof setTimeout> | null = null;

/** Holds the bridge's tab for the action about to run: a release landing now would take it away. */
function cancelRelease(): void {
  if (releaseTimer) clearTimeout(releaseTimer);
  releaseTimer = null;
}

/** Releases the bridge's tab after a short idle, so an opener can still read what it opened. */
function releaseSoon(): void {
  cancelRelease();
  releaseTimer = setTimeout(() => {
    releaseTimer = null;
    void forgetBridgeChat();
  }, RELEASE_IDLE_MS);
}

/** Closes the bridge's tab and its hidden native view: no chat shows it, and none should keep it. */
function forgetBridgeChat(): Promise<void> {
  return import("@/features/browser/store")
    .then(({ useBrowserStore }) => useBrowserStore.getState().forgetChat(MCP_BRIDGE_CHAT))
    .catch(() => undefined);
}

/** Starts the bridge in the desktop shell, and only once: the returned call stops the bridge this
 *  call started, and leaves one another caller started alone. */
export function ensureMcpBrowserBridge(): () => void {
  if (!isTauri || bridge) return () => undefined;
  const started: Bridge = { controller: new AbortController(), loop: Promise.resolve() };
  bridge = started;
  started.loop = run(started)
    .catch(() => undefined)
    .finally(() => {
      if (bridge === started) bridge = null;
    });
  return () => stopBridge(started);
}

/** Ends the bridge wherever it was started from: the app is going away. */
export function stopMcpBrowserBridge(): void {
  stopBridge(bridge);
}

function stopBridge(target: Bridge | null): void {
  if (!target || bridge !== target) return;
  bridge = null;
  target.controller.abort();
  cancelRelease();
  // Its hidden tabs outlive the run's chat, and a restored session would keep them: close them.
  void forgetBridgeChat();
}

async function run(started: Bridge): Promise<void> {
  const { signal } = started.controller;
  // With the loop's first turn, not at module load: the agent store is a chunk of its own.
  let agents: typeof import("./agent-store");
  try {
    agents = await bridgeStore();
  } catch {
    return;
  }
  let failures = 0;
  while (!signal.aborted) {
    // The Browser tool is off for chats: nothing is read in the panel until the user turns it on, and
    // turning it on is what wakes this loop. No claim is made while it is off.
    if (!agents.useBrowserAgentStore.getState().enabled) {
      await whenEnabled(agents.useBrowserAgentStore, signal);
      continue;
    }
    // The endpoint answers the owner session; signed out there is nothing it would hand out.
    if (!hasSettledAuthSession()) {
      await waitForAuth(signal);
      continue;
    }
    const askedAt = Date.now();
    let claimed: unknown;
    try {
      claimed = await claim(signal);
      // A network error, a 5xx or a 401 all land here: the next try waits longer than the last.
      failures = 0;
    } catch (error) {
      if (signal.aborted) return;
      failures += 1;
      note(error);
      await sleep(backoffMs(failures), signal);
      continue;
    }
    const action = (claimed as { action?: unknown } | null)?.action;
    if (action == null) {
      await sleep(Math.max(0, MIN_POLL_GAP_MS - (Date.now() - askedAt)), signal);
      continue;
    }
    if (signal.aborted) return;
    // Awaited: the executor runs one action per session at a time, and the caller's next action is
    // only handed out once this one answered.
    cancelRelease();
    try {
      await hand(action, signal);
    } finally {
      // The tab outlives the action by a short idle, so the snapshot one web_browse reads after its
      // navigation still finds the page; nothing releases it mid-action.
      if (!signal.aborted) releaseSoon();
    }
  }
}

/** The agent store, loaded once: the bridge only needs it after its first wake. */
let agentStore: Promise<typeof import("./agent-store")> | null = null;
function bridgeStore(): Promise<typeof import("./agent-store")> {
  return (agentStore ??= import("./agent-store"));
}

/** Until the Browser tool is turned on, a change lands, or the loop stops. A capped wait means a lost
 * change costs one idle wake, never a bridge that polls forever or never again. */
function whenEnabled(agents: typeof import("./agent-store")["useBrowserAgentStore"], signal: AbortSignal): Promise<void> {
  if (signal.aborted || agents.getState().enabled) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      unsubscribe();
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const unsubscribe = agents.subscribe(() => {
      if (agents.getState().enabled) done();
    });
    const timer = setTimeout(done, DISABLED_WAIT_MS);
    signal.addEventListener("abort", done, { once: true });
  });
}

/** The claim, bounded by the server's own wait plus a margin, and cut short when the bridge stops. */
async function claim(signal: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, CLAIM_TIMEOUT_MS);
  try {
    const response = await authFetch(`${CLAIM_PATH}?wait=${CLAIM_WAIT_S}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`claim answered ${response.status}`);
    return (await response.json()) as unknown;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}

async function hand(raw: unknown, signal: AbortSignal): Promise<void> {
  let executor: typeof import("./executor");
  try {
    // With the first action, not at startup: the panel's code is a chunk of its own.
    executor = await import("./executor");
  } catch {
    await postFailure(raw, "the browser panel could not load; ask the user to reload Unsloth.");
    return;
  }
  const action = executor.parseBrowserAction(raw);
  if (!action) {
    await postFailure(raw, "the browser action was not one this build understands.");
    return;
  }
  try {
    await executor.runBrowserAction(action, contextFor(signal));
  } catch {
    // runBrowserAction answers its own failures; a throw here belongs to its queue, not the action.
  }
}

/** The executor's view of an external caller: always this one chat, always its own session, and
 *  never the user's chat, history or full access. */
function contextFor(signal: AbortSignal): ActionContext {
  return {
    runKey: MCP_BRIDGE_CHAT,
    scope: MCP_BRIDGE_CHAT,
    chat: MCP_BRIDGE_CHAT,
    // The user's chat is elsewhere and its pages are their own: an external caller's stay out of history.
    temporary: true,
    userText: CALLER,
    fullAccess: false,
    signal,
  };
}

/** Nothing ran, so nothing will answer: an unanswered action waits out its whole timeout instead. */
async function postFailure(raw: unknown, error: string): Promise<void> {
  const action = raw as Record<string, unknown> | null;
  const id = typeof action?.id === "string" ? action.id : "";
  if (!id) return;
  const sessionId = typeof action?.session_id === "string" ? action.session_id : "";
  try {
    await authFetch(RESULT_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: id, session_id: sessionId, result: { ok: false, error } }),
    });
  } catch {
    // Lost, and the caller falls back on its timeout as it would for any lost answer.
  }
}

/** Until a signed-in session settles, or a beat later to look again. */
function waitForAuth(signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      window.removeEventListener(AUTH_SESSION_STORED_EVENT, onStored);
      signal.removeEventListener("abort", done);
      clearTimeout(timer);
      resolve();
    };
    const onStored = () => {
      if (hasAuthToken()) done();
    };
    const timer = setTimeout(done, AUTH_WAIT_MS);
    window.addEventListener(AUTH_SESSION_STORED_EVENT, onStored);
    signal.addEventListener("abort", done, { once: true });
  });
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0 || signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

/** Doubling from a second to the cap, jittered so a whole app's retries don't march in step. */
function backoffMs(failures: number): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_FIRST_MS * 2 ** Math.min(failures - 1, 5));
  return Math.round(base * (0.75 + Math.random() * 0.5));
}

function note(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  if (noted.has(message)) return;
  noted.add(message);
  console.warn(`mcp bridge: ${message}; retrying with backoff`);
}
