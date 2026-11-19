# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""``GET /api/browser/actions/claim``: the panel's long poll for an action an agent announced, the
empty answer that keeps it polling, and the account scoping that decides who may take one."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.authentication import get_current_subject
import routes.browser as browser_mod
from state import browser_actions
from utils.account_context import current_account_id

ENDPOINT = "/api/browser/actions/claim"
ACTION = {
    "id": "a1",
    "session_id": "",
    "op": "navigate",
    "url": "https://example.com/",
    "allowed_domains": [],
    "blocked_domains": [],
    "timeout_s": 40.0,
}


def _app(authenticated = True) -> FastAPI:
    app = FastAPI()
    app.include_router(browser_mod.router, prefix = "/api/browser")
    if authenticated:
        app.dependency_overrides[get_current_subject] = lambda: "unsloth"
    return app


@pytest.fixture
def client():
    with TestClient(_app()) as http:
        yield http


@pytest.fixture(autouse = True)
def _empty_queues():
    yield
    browser_actions._pending.clear()
    browser_actions._announced.clear()


def _announce(action_id = "a1", *, account = None) -> tuple[dict, dict]:
    account = current_account_id() if account is None else account
    slot = browser_actions.begin_browser_action(action_id, "", account)
    action = {**ACTION, "id": action_id}
    browser_actions.announce_browser_action(action_id, slot, action)
    return slot, action


def test_no_key_is_refused():
    with TestClient(_app(authenticated = False)) as http:
        assert http.get(ENDPOINT, params = {"wait": 0}).status_code == 401


def test_a_poll_with_nothing_to_take_answers_null_after_its_wait(client):
    started = time.monotonic()
    response = client.get(ENDPOINT, params = {"wait": 0.1})
    assert response.status_code == 200
    assert response.json() == {"action": None}
    # A long poll: it parked for the wait instead of answering the moment it found nothing.
    assert 0.09 <= time.monotonic() - started < 5


def test_an_announced_action_comes_back_exactly_as_the_chat_emits_it(client):
    _slot, action = _announce()
    response = client.get(ENDPOINT, params = {"wait": 0})
    assert response.status_code == 200
    assert response.json() == {"action": action}
    # A replayed action would be run twice, so the answer may never be stored.
    assert response.headers["cache-control"] == "no-store"
    # The same panel asking again finds nothing: one action goes to one poll.
    assert client.get(ENDPOINT, params = {"wait": 0}).json() == {"action": None}


def test_a_parked_poll_is_woken_by_an_action_announced_later(client):
    threading.Timer(0.05, _announce).start()
    response = client.get(ENDPOINT, params = {"wait": 5})
    assert response.json() == {"action": ACTION}


def test_another_accounts_action_is_left_for_its_own_panel(client):
    _announce(account = "alice")
    assert client.get(ENDPOINT, params = {"wait": 0.1}).json() == {"action": None}
    assert browser_actions.claim_browser_action("alice", 0) == ACTION


def test_an_action_whose_asker_stopped_waiting_is_not_delivered(client):
    slot, _action = _announce()
    browser_actions.discard_browser_action("a1", slot)
    assert client.get(ENDPOINT, params = {"wait": 0.1}).json() == {"action": None}


@pytest.mark.parametrize("wait", ["-1", "31", "not-a-number"])
def test_a_wait_past_its_bound_is_refused(client, wait):
    assert client.get(ENDPOINT, params = {"wait": wait}).status_code == 422


def test_an_api_key_cannot_claim_the_panels_actions(monkeypatch, client):
    # An MCP key must not be handed an action: it would run in whichever panel is signed in, on that
    # user's pages. Only the panel itself claims, so a key-bearing request is refused and the action
    # is left for the panel.
    # By name: the route imports the helper inside its body, so the patched module attribute is what
    # it calls.
    monkeypatch.setattr("utils.account_context.via_api_key_context", lambda: True)
    _slot, _action = _announce()
    response = client.get(ENDPOINT, params = {"wait": 0})
    assert response.status_code == 403
    assert "browser panel" in response.json()["detail"]
    assert browser_actions.claim_browser_action(current_account_id(), 0) == ACTION


def test_the_poll_defaults_are_the_panels_own_interval(client):
    # The default a panel gets by leaving wait out, and the most any caller may ask for.
    assert browser_mod.CLAIM_WAIT_S == 20.0 and browser_mod.CLAIM_WAIT_MAX_S == 30.0
