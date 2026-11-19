// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

import { RouterProvider } from "@tanstack/react-router";
import { useEffect } from "react";
import { isTauri } from "@/lib/api-base";
import { useDesktopShellReady } from "./desktop-shell-ready";
import { router } from "./router";

/** Runs the MCP browser bridge for as long as the desktop window is open: an outside caller's
 *  browser action is claimed, acted on in the browser panel, and answered. A Studio hosted in a
 *  browser has no panel to drive it with, so it never polls. The bridge is loaded on demand: the
 *  panel's code is a chunk of its own, and only the desktop shell ever needs it. */
function useMcpBrowserBridge(): void {
  // The desktop shell is ready once its backend runs and its sign-in settled.
  const shellReady = useDesktopShellReady();
  useEffect(() => {
    if (!isTauri || !shellReady) return;
    let stopped = false;
    let stop: (() => void) | null = null;
    void import("@/features/browser/agent/mcp-bridge").then(
      ({ ensureMcpBrowserBridge }) => {
        stop = ensureMcpBrowserBridge();
        // Stopped while the module loaded: the disposer only ends a bridge this call started.
        if (stopped) stop();
      },
      () => undefined,
    );
    return () => {
      stopped = true;
      stop?.();
    };
  }, [shellReady]);
}

export function App() {
  useMcpBrowserBridge();
  return <RouterProvider router={router} />;
}
