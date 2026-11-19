// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

// The bridge's neighbours, stubbed for tests/mcp-browser-bridge.test.ts: the network through
// globalThis.mcpFetch, the executor through mcpParse/mcpRun, the browser store's forgetChat through
// mcpForget, the agent store's Browser-tool flag through globalThis.mcpAgentStore, and the desktop
// shell as a given (isTauri true).
const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;

const STUBS = {
  "@/features/auth": stub(`
    export const AUTH_SESSION_STORED_EVENT = "unsloth:auth-session-stored";
    export const hasAuthToken = () => globalThis.mcpSignedIn !== false;
    export const hasSettledAuthSession = () => globalThis.mcpSignedIn !== false;
    export const authFetch = (url, init) => globalThis.mcpFetch(String(url), init);
  `),
  "@/lib/api-base": stub("export const isTauri = true;"),
  "@/features/browser/store": stub(`
    export const useBrowserStore = {
      getState: () => ({ forgetChat: (chat) => void (globalThis.mcpForgotten ??= []).push(chat) }),
    };
  `),
  // The real store's shape for what the bridge reads: the composer's Browser-tool flag, and a
  // subscription that fires when it changes. On by default here, so the poll tests are about the poll.
  "./agent-store": stub(`
    const state = (globalThis.mcpAgentState ??= { enabled: globalThis.mcpBrowserEnabled !== false });
    const useBrowserAgentStore = {
      getState: () => state,
      subscribe: (listener) => {
        globalThis.mcpAgentSubs ??= new Set();
        globalThis.mcpAgentSubs.add(listener);
        return () => void globalThis.mcpAgentSubs.delete(listener);
      },
      setEnabled: (enabled) => {
        state.enabled = enabled;
        for (const listener of globalThis.mcpAgentSubs ?? []) listener(state);
      },
    };
    globalThis.mcpAgentStore = useBrowserAgentStore;
    export { useBrowserAgentStore };
    export const MCP_BRIDGE_CHAT = "mcp-bridge";
  `),
  "./executor": stub(`
    export const parseBrowserAction = (raw) => globalThis.mcpParse(raw);
    export const runBrowserAction = (action, context) => globalThis.mcpRun(action, context);
  `),
};

export function resolve(specifier, context, next) {
  if (STUBS[specifier]) return { url: STUBS[specifier], shortCircuit: true };
  // Only the executor is the bridge's own; "./executor" is a type-only import until the first action.
  if (specifier === "./executor" && context.parentURL?.includes("/src/features/browser/agent/")) {
    return { url: STUBS["./executor"], shortCircuit: true };
  }
  // The rest of the agent directory resolves as TypeScript, as the app's bundler would.
  if (
    specifier.startsWith("./") &&
    context.parentURL?.includes("/src/features/browser/agent/") &&
    !/\.\w+$/.test(specifier)
  ) {
    return next(`${specifier}.ts`, context);
  }
  return next(specifier, context);
}
