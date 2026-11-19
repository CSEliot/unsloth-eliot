# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""Browser tool slots answered by POST /api/inference/browser-action-result; first answer wins;
registered before announced; scoped to session and account. An action asked for outside a chat (MCP
``web_browse``) is also queued here for the browser panel to claim by long poll:
``announce_browser_action``, then ``claim_browser_action``."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

_lock = threading.Lock()
_pending: dict[str, dict] = {}
# Actions waiting for the browser panel to ask for them, oldest first, each with the slot that
# answers it. Its own condition, not _lock: a claim parked for up to half a minute must never delay
# the answer to a chat's slot, and no path holds the two locks together.
_claim_lock = threading.Condition()
_announced: "OrderedDict[str, tuple[dict, dict]]" = OrderedDict()


def new_action_id() -> str:
    return secrets.token_urlsafe(16)


def begin_browser_action(action_id: str, session_id: Optional[str], account_id: str) -> dict:
    slot = {
        "event": threading.Event(),
        "result": None,
        "session": session_id or "",
        "account": account_id or "",
    }
    with _lock:
        _pending[action_id] = slot
    return slot


def announce_browser_action(action_id: str, slot: dict, action: dict) -> None:
    """Queue ``action`` for the browser panel to claim, after ``begin_browser_action`` registered it.

    Registering first is what keeps first-answer-wins: a panel that claims and answers straight away
    still finds its slot, and a slot dropped before it was announced is never handed out.
    """
    with _claim_lock:
        _announced[action_id] = (slot, action)
        _claim_lock.notify_all()


def claim_browser_action(account_id: str, wait: float = 0.0) -> Optional[dict]:
    """The oldest action this account's browser panel may run, or None if none arrives within ``wait``.

    ``wait`` 0 is a plain non-blocking pop. One action goes to one panel: the entry is taken under
    the lock, so two polls cannot both receive it. Another account's action stays queued for its own
    panel rather than being dropped, and an action whose asker has stopped waiting is dropped instead
    of handed out, so the panel never runs a page nobody will read.
    """
    account = account_id or ""
    deadline = time.monotonic() + max(0.0, float(wait))
    while True:
        with _claim_lock:
            while True:
                oldest = next(
                    (
                        (action_id, entry)
                        for action_id, entry in _announced.items()
                        if entry[0]["account"] == account
                    ),
                    None,
                )
                if oldest is not None:
                    action_id, (slot, action) = oldest
                    _announced.pop(action_id, None)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                _claim_lock.wait(remaining)
        if _waiting(action_id, slot):
            return action
        if time.monotonic() >= deadline:
            return None


def _waiting(action_id: str, slot: dict) -> bool:
    """Whether the asker still waits on this slot. Reads _lock on its own, never under _claim_lock."""
    with _lock:
        return _pending.get(action_id) is slot and not slot["event"].is_set()


def discard_browser_action(action_id: str, slot: dict) -> None:
    """Drop a slot that will never be waited on (the stream closed before the tab was asked)."""
    _forget(action_id, slot)


def _forget(action_id: str, slot: dict) -> None:
    """Remove a slot and any action announced for it, one lock at a time so the two never nest."""
    with _lock:
        if _pending.get(action_id) is slot:
            _pending.pop(action_id, None)
    with _claim_lock:
        announced = _announced.get(action_id)
        if announced is not None and announced[0] is slot:
            _announced.pop(action_id, None)


def wait_browser_action(
    slot: dict,
    action_id: str,
    cancel_event = None,
    timeout: Optional[float] = None,
) -> tuple[str, Optional[dict]]:
    """Blocks until answered, cancelled or expired; always removes its own slot."""
    waited = 0.0
    try:
        while not slot["event"].wait(timeout = 0.25):
            if cancel_event is not None and cancel_event.is_set():
                return "cancelled", None
            waited += 0.25
            if timeout is not None and waited >= timeout:
                return "expired", None
        return "answered", slot["result"]
    finally:
        _forget(action_id, slot)


def resolve_browser_action(
    action_id: str, result: Any, session_id: Optional[str], account_id: str
) -> bool:
    if not action_id or not isinstance(result, dict):
        return False
    with _lock:
        slot = _pending.get(action_id)
        if not slot or slot["event"].is_set():
            return False
        if slot["session"] != (session_id or "") or slot["account"] != (account_id or ""):
            return False
        slot["result"] = result
        slot["event"].set()
    return True
