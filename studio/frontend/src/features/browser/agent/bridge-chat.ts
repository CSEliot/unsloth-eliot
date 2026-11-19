// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

/** The chat an outside MCP client's actions run under. Not a thread's: it has no chat of its own, and
 *  no tab strip shows it, so its question is asked in the panel the user is looking at instead.
 *
 *  On its own here so the bridge can name it without loading the agent store, which the bridge only
 *  needs once an action arrives. */
export const MCP_BRIDGE_CHAT = "mcp-bridge";
