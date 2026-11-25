# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""The fork's action dict and the chat path's must stay one shape.

A chat tab is told about an action by the stream, the desktop app's panel by the claim route. The
fork builds its own copy of that dict so ``core.inference.browser_agent`` can stay upstream's file;
this drives the upstream path with a stub answerer and compares the two, so a change there fails
here instead of leaving the panel unable to parse an action.
"""

from __future__ import annotations

from core.inference import browser_agent
from state import browser_actions
from studio_mcp.action_payload import action_payload


def test_the_fork_payload_matches_what_a_chat_tab_is_sent(monkeypatch):
    seen: dict = {}

    def capture(event: dict) -> None:
        seen.update(event["browser_action"])

    # The panel never answers: the chat path announces, waits, and gives up, which is enough to see
    # the dict it sent. The stub drops the slot the way the real wait does, so nothing leaks into a
    # later test's claim.
    def give_up(slot, action_id, cancel, wait):
        browser_actions.discard_browser_action(action_id, slot)
        return ("expired", None)

    monkeypatch.setattr(browser_actions, "wait_browser_action", give_up)
    browser_agent.execute_browser_tool(
        "browser_snapshot", {}, output_callback = capture, website_policy = None
    )

    request = {"op": "snapshot", "full_page": False}
    ours = action_payload(seen["id"], seen["session_id"], request, {"allowedDomains": [], "blockedDomains": []}, seen["timeout_s"])
    assert ours == seen
    # And the panel parses exactly these fields: snake_case, no nesting, no extra wrapper.
    assert set(seen) == {"id", "session_id", "allowed_domains", "blocked_domains", "timeout_s"} | set(request)
