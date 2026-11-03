"""The "grant recognizer_loop:utterance" hint is logged only when the hub refuses one.

It used to be a WARNING at every start, which is false on a managed hub
(the platform grants the type) and misleads whoever reads the log. The hub
says when it refuses a message type: ``hive.policy.denied`` with code
``acl_disallowed_type``. The hint is logged then, once.
"""
from unittest.mock import MagicMock

import discord
import pytest

import hivemind_discord_bridge as bridge_module
from hivemind_discord_bridge import HiveMindDiscordBridge

from conftest import FakeDiscord, FakeHub, wait_for


@pytest.fixture
def logged(monkeypatch):
    lines = []
    log = MagicMock()
    for level in ("debug", "info", "warning", "error", "exception"):
        getattr(log, level).side_effect = (
            lambda *a, _level=level, **k: lines.append((_level, " ".join(map(str, a)))))
    monkeypatch.setattr(bridge_module, "LOG", log)
    return lines


def _hints(lines):
    return [(level, text) for level, text in lines if "allow-msg" in text]


def test_starting_says_nothing_about_hub_permissions(monkeypatch, logged):
    async def login(self, token):
        return None

    async def connect(self, *, reconnect=True):
        return None  # the gateway session ends at once

    monkeypatch.setattr(discord.Client, "login", login)
    monkeypatch.setattr(discord.Client, "connect", connect)
    hub = FakeHub()
    try:
        HiveMindDiscordBridge(token="tok", client=hub).start()
    finally:
        hub.close()
    assert logged, "the bridge logged nothing; the test would prove nothing"
    assert _hints(logged) == []


def _refuse(code):
    return {"denied_type": "recognizer_loop:utterance", "code": code}


def _bridge():
    hub = FakeHub()
    bridge = HiveMindDiscordBridge(client=hub, discord_client=FakeDiscord())
    bridge.connect_hivemind()
    return bridge, hub


def test_a_refused_message_type_names_the_grant_once(logged):
    bridge, hub = _bridge()
    try:
        for _ in range(3):
            hub.send("hive.policy.denied", _refuse("acl_disallowed_type"), {})
        assert wait_for(lambda: len(hub.held) == 3)
    finally:
        hub.close()
    hints = _hints(logged)
    assert len(hints) == 1
    level, text = hints[0]
    assert level == "warning"
    assert "hivemind-core allow-msg recognizer_loop:utterance" in text


def test_other_refusals_do_not_mention_the_grant(logged):
    bridge, hub = _bridge()
    try:
        hub.send("hive.policy.denied", _refuse("intent_quota_exceeded"), {})
        assert wait_for(lambda: len(hub.held) == 1)
    finally:
        hub.close()
    assert _hints(logged) == []
    assert any("intent_quota_exceeded" in text for _, text in logged)
