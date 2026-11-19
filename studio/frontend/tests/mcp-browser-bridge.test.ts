// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

// The desktop shell's MCP browser bridge: one long poll of the claim endpoint at a time, each
// claimed action handed to the executor as the panel would run it, and no hot loop when the
// endpoint answers empty or the network fails.

import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

type Handed = { action: unknown; context: Record<string, unknown> };

const claims: string[] = [];
const answered: { url: string; body: Record<string, unknown> }[] = [];
const handed: Handed[] = [];
let answers: (unknown | "error")[] = [];

const noop = () => undefined;
const reply = (body: unknown) =>
  Promise.resolve(
    new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }),
  );

Object.assign(globalThis, {
  window: Object.assign(globalThis, { addEventListener: noop, removeEventListener: noop }),
});

const answer = (url: string, init?: RequestInit) => {
  if (url.endsWith("/api/inference/browser-action-result")) {
    answered.push({ url, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
    return reply({ ok: true });
  }
  claims.push(url);
  const next = answers.shift();
  if (next === "error") return Promise.reject(new TypeError("network down"));
  return reply({ action: next ?? null });
};
const parse = (raw: unknown) =>
  raw && typeof raw === "object" && typeof (raw as Record<string, unknown>).op === "string" ? raw : null;
const run = (action: unknown, context: Record<string, unknown>) => {
  handed.push({ action, context });
  return Promise.resolve();
};

Object.assign(globalThis, { mcpFetch: answer, mcpParse: parse, mcpRun: run });

register("./helpers/mcp-bridge-resolver.mjs", import.meta.url);
const { ensureMcpBrowserBridge } = await import("../src/features/browser/agent/mcp-bridge.ts");

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check: () => boolean, timeoutMs = 5_000): Promise<void> {
  const ended = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > ended) throw new Error("timed out waiting");
    await wait(10);
  }
}
const mcpForgotten = () => (globalThis as { mcpForgotten?: string[] }).mcpForgotten ?? [];

/** Each test starts from an empty log and its own answers: the bridge's counters are its own. */
function reset(): void {
  claims.length = 0;
  answered.length = 0;
  handed.length = 0;
  answers = [];
  // A test that held its action leaves that runner behind; the next one wants the ordinary one.
  Object.assign(globalThis, { mcpRun: run, mcpForgotten: [] });
}

const agentStore = () =>
  (globalThis as { mcpAgentStore?: { setEnabled: (enabled: boolean) => void } }).mcpAgentStore;

test("nothing is claimed while the Browser tool is off, and turning it on resumes the poll", async () => {
  reset();
  // The composer's flag is off, as it is on a fresh install: an outside caller must not be served
  // browsing the user turned off.
  (globalThis as { mcpBrowserEnabled?: boolean }).mcpBrowserEnabled = false;
  (globalThis as { mcpAgentState?: { enabled: boolean } }).mcpAgentState = { enabled: false };
  answers = [{ id: "a0", session_id: "s0", op: "navigate", url: "https://example.com/" }];
  const stop = ensureMcpBrowserBridge();
  try {
    await wait(1_200);
    assert.equal(claims.length, 0, "claimed while the Browser tool was off");
    assert.equal(handed.length, 0);
    agentStore()?.setEnabled(true);
    await until(() => handed.length === 1);
    assert.ok(claims.length >= 1);
  } finally {
    stop();
  }
});

test("a claimed action runs under the bridge's own chat, with no full access", async () => {
  reset();
  answers = [{ id: "a1", session_id: "s1", op: "navigate", url: "https://example.com/" }];
  const stop = ensureMcpBrowserBridge();
  try {
    await until(() => handed.length === 1);
    assert.equal(claims[0], "/api/browser/actions/claim?wait=20");
    const { action, context } = handed[0] as Handed;
    assert.deepEqual(action, { id: "a1", session_id: "s1", op: "navigate", url: "https://example.com/" });
    assert.equal(context.runKey, "mcp-bridge");
    assert.equal(context.scope, "mcp-bridge");
    assert.equal(context.chat, "mcp-bridge");
    // An external caller's pages stay out of the user's history and the run is never the user's.
    assert.equal(context.temporary, true);
    assert.equal(context.fullAccess, false);
    assert.match(String(context.userText), /MCP client/);
    assert.equal((context.signal as AbortSignal).aborted, false);
  } finally {
    stop();
  }
});

test("an empty answer is not answered with another request at once", async () => {
  reset();
  const stop = ensureMcpBrowserBridge();
  try {
    await until(() => claims.length === 1);
    await wait(500);
    assert.equal(claims.length, 1, "polled again inside the minimum gap");
  } finally {
    stop();
  }
});

test("a network error backs off before asking again", async () => {
  reset();
  answers = ["error", null];
  const stop = ensureMcpBrowserBridge();
  try {
    await until(() => claims.length >= 1);
    const failedAt = Date.now();
    await until(() => claims.length >= 2);
    // The first retry waits a second, jittered no lower than three quarters of it.
    assert.ok(Date.now() - failedAt >= 600, "asked again too soon after a network error");
  } finally {
    stop();
  }
});

test("an action this build cannot read is answered rather than left to time out", async () => {
  reset();
  answers = [{ id: "a2", session_id: "s2" }];
  const stop = ensureMcpBrowserBridge();
  try {
    await until(() => answered.length === 1);
    assert.equal(answered[0]?.url, "/api/inference/browser-action-result");
    assert.deepEqual(answered[0]?.body, {
      action_id: "a2",
      session_id: "s2",
      result: { ok: false, error: "the browser action was not one this build understands." },
    });
  } finally {
    stop();
  }
});

test("stopping ends the poll, aborts the action in flight, and leaves no hidden tabs", async () => {
  reset();
  answers = [{ id: "a4", session_id: "s4", op: "snapshot" }];
  let release: () => void = noop;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  // Held open, so the stop lands while an action is running, as it would at app quit.
  Object.assign(globalThis, {
    mcpRun: (action: unknown, context: Record<string, unknown>) => {
      handed.push({ action, context });
      return held;
    },
  });
  const stop = ensureMcpBrowserBridge();
  try {
    await until(() => handed.length === 1);
    const { signal } = (handed[0] as Handed).context as { signal: AbortSignal };
    assert.equal(signal.aborted, false);
    const asked = claims.length;
    stop();
    assert.equal(signal.aborted, true);
    // Its tab lived in a chat no window shows: closed with the bridge, not restored next launch.
    await until(() => mcpForgotten().includes("mcp-bridge"));
    release();
    await wait(1_300);
    assert.equal(claims.length, asked, "polled after stopping");
  } finally {
    stop();
    release();
  }
});

test("the bridge's tab is released after its action's idle, not held for the whole session", async () => {
  reset();
  answers = [{ id: "a5", session_id: "s5", op: "navigate", url: "https://example.com/" }];
  const stop = ensureMcpBrowserBridge();
  try {
    await until(() => handed.length === 1);
    // The action finished; the tab stays for a beat so a follow-up snapshot can still read the page.
    await wait(500);
    assert.equal(mcpForgotten().includes("mcp-bridge"), false, "released while the action could still be read");
    // Then it goes, with the native view behind it, instead of living until the app quits.
    await until(() => mcpForgotten().includes("mcp-bridge"), 8_000);
  } finally {
    stop();
  }
});
