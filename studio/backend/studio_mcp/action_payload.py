# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""The dict one browser action travels in, on both of its roads.

A chat tab learns about an action through the stream (``core.inference.browser_agent`` builds the
event inline), and the desktop app's browser panel learns about it through GET
/api/browser/actions/claim. Both answerers parse the same fields, so the fork builds its copy here
instead of editing the chat path: the two must stay identical, which is what
``tests/test_mcp_tools_web.py`` pins by driving the chat path and comparing.
"""

from __future__ import annotations

from typing import Optional


def action_payload(
    action_id: str,
    session_id: Optional[str],
    request: dict,
    website_policy: dict,
    timeout_s: float,
) -> dict:
    """The one shape an action travels in: its id, the session it answers for, the request's own
    fields, the site policy the tab enforces and how long the asker waits."""
    return {
        "id": action_id,
        "session_id": session_id or "",
        **request,
        "allowed_domains": list(website_policy["allowedDomains"]),
        "blocked_domains": list(website_policy["blockedDomains"]),
        # The tab drops an unanswered question once this side stops waiting, so a late Allow can't run.
        "timeout_s": timeout_s,
    }
