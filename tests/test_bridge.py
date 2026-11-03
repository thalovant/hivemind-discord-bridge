"""Unit tests: construct the bridge offline and drive it with mocks.

No live Discord or HiveMind connection is made. A pre-built mock
discord.Client and a pre-built mock HiveMessageBusClient are injected so
the bridge's __init__ never touches the network.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


def _make_bridge(**kwargs):
    from hivemind_discord_bridge import HiveMindDiscordBridge

    fake_client = MagicMock(name="HiveMessageBusClient")
    fake_discord = MagicMock(name="discord.Client")
    fake_discord.event = MagicMock(side_effect=lambda f: f)
    fake_discord.user = MagicMock(name="BotUser")
    fake_discord.user.id = 999
    fake_discord.get_channel = MagicMock(return_value=None)
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock()
    fake_discord.fetch_channel = AsyncMock(return_value=fake_channel)

    bridge = HiveMindDiscordBridge(client=fake_client, discord_client=fake_discord,
                                   **kwargs)
    return bridge, fake_client, fake_discord, fake_channel


def _fake_message(text="hello world", user_id=1, username="alice",
                  channel_id=42, is_dm=False):
    message = MagicMock()
    message.content = text
    author = MagicMock()
    author.id = user_id
    author.name = username
    message.author = author
    channel = MagicMock(spec=discord.DMChannel if is_dm else discord.TextChannel)
    channel.id = channel_id
    message.channel = channel
    return message


def test_import_package_and_version():
    import hivemind_discord_bridge
    from hivemind_discord_bridge.version import __version__

    assert isinstance(__version__, str)
    assert __version__
    assert hivemind_discord_bridge.platform.startswith("HiveMindDiscordBridge")


def test_construct_bridge_without_connecting():
    bridge, fake_client, fake_discord, _ = _make_bridge()
    fake_discord.event.assert_called_once()
    assert bridge._started is False
    fake_client.connect.assert_not_called()


def test_token_required_without_injected_client():
    from hivemind_discord_bridge import HiveMindDiscordBridge

    with pytest.raises(ValueError):
        HiveMindDiscordBridge(client=MagicMock())


def test_connect_hivemind_calls_connect_once_and_registers_handlers():
    """connect_hivemind() must call connect() exactly once, never run_forever()."""
    bridge, fake_client, fake_discord, _ = _make_bridge()
    bridge.connect_hivemind()

    fake_client.connect.assert_called_once_with(
        site_id="discord", handshake_max_retries=10)
    fake_client.run_forever.assert_not_called()
    assert bridge._connected.is_set()
    registered = {call.args[0] for call in fake_client.on_mycroft.call_args_list}
    assert {"speak", "complete_intent_failure", "hive.complete_intent_failure"} <= registered


def test_connect_hivemind_bounds_handshake_retries():
    """A stalled/unreachable hub must not hang connect() forever: the
    handshake_max_retries kwarg must always be passed, and non-None."""
    from hivemind_discord_bridge import DEFAULT_HANDSHAKE_MAX_RETRIES

    bridge, fake_client, fake_discord, _ = _make_bridge()
    bridge.connect_hivemind()

    _, kwargs = fake_client.connect.call_args
    assert kwargs.get("handshake_max_retries") is not None
    assert kwargs["handshake_max_retries"] == DEFAULT_HANDSHAKE_MAX_RETRIES


def test_inbound_message_forwarded_to_hivemind_after_connect():
    from hivemind_bus_client import HiveMessage, HiveMessageType

    # every channel message, as a bot that reads them all forwards them
    bridge, fake_client, fake_discord, _ = _make_bridge(require_mention=False)
    bridge.connect_hivemind()

    message = _fake_message(text="turn on the lights", user_id=1,
                            username="alice", channel_id=42)
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_called_once()
    sent = fake_client.emit.call_args[0][0]
    assert isinstance(sent, HiveMessage)
    assert sent.msg_type == HiveMessageType.BUS
    payload = sent.payload
    assert payload.msg_type == "recognizer_loop:utterance"
    assert payload.data["utterances"] == ["turn on the lights"]
    assert payload.context["channel_id"] == 42
    assert payload.context["user"]["discord_username"] == "alice"
    assert payload.context["session"]["session_id"] == "discord-42"


def test_no_forward_before_hivemind_connected():
    """Messages arriving before connect_hivemind() must be dropped, not queued."""
    bridge, fake_client, fake_discord, _ = _make_bridge()
    # deliberately not calling bridge.connect_hivemind()

    message = _fake_message()
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_not_called()


def test_bot_own_message_is_skipped():
    bridge, fake_client, fake_discord, _ = _make_bridge()
    bridge.connect_hivemind()

    message = _fake_message(user_id=999)  # matches fake_discord.user.id
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_not_called()


def test_non_text_message_is_ignored():
    bridge, fake_client, fake_discord, _ = _make_bridge()
    bridge.connect_hivemind()

    message = _fake_message(text="")
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_not_called()


def test_disallowed_channel_is_ignored():
    bridge, fake_client, fake_discord, _ = _make_bridge(allowed_channels=[1, 2, 3])
    bridge.connect_hivemind()

    message = _fake_message(channel_id=999)
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_not_called()


def test_allowed_channel_is_forwarded():
    bridge, fake_client, fake_discord, _ = _make_bridge(allowed_channels=[42],
                                                        require_mention=False)
    bridge.connect_hivemind()

    message = _fake_message(channel_id=42)
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_called_once()


def test_a_dm_cannot_step_around_the_channel_list():
    """With channels listed, a stranger's DM is not a way in (dm policy auto)."""
    bridge, fake_client, fake_discord, _ = _make_bridge(allowed_channels=[1, 2, 3])
    bridge.connect_hivemind()
    assert bridge.dm_policy == "deny"

    message = _fake_message(channel_id=999, is_dm=True)
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_not_called()


def test_dms_can_be_allowed_next_to_a_channel_list():
    bridge, fake_client, fake_discord, _ = _make_bridge(allowed_channels=[1, 2, 3],
                                                        dm_policy="allow")
    bridge.connect_hivemind()

    message = _fake_message(channel_id=999, is_dm=True)
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_called_once()


def test_without_a_channel_list_dms_are_allowed_by_default():
    bridge, fake_client, fake_discord, _ = _make_bridge()
    bridge.connect_hivemind()
    assert bridge.dm_policy == "allow"

    message = _fake_message(channel_id=999, is_dm=True)
    asyncio.run(bridge.on_message(message))

    fake_client.emit.assert_called_once()


def test_dms_can_be_refused_without_a_channel_list():
    bridge, fake_client, fake_discord, _ = _make_bridge(dm_policy="deny",
                                                        require_mention=False)
    bridge.connect_hivemind()

    asyncio.run(bridge.on_message(_fake_message(channel_id=999, is_dm=True)))
    fake_client.emit.assert_not_called()
    # a server channel still works
    asyncio.run(bridge.on_message(_fake_message(text="hi", channel_id=7)))
    fake_client.emit.assert_called_once()


def test_an_unknown_dm_policy_is_refused():
    with pytest.raises(ValueError):
        _make_bridge(dm_policy="sometimes")


def test_speak_schedules_send_on_discord_loop():
    """handle_speak must route the hub's reply back to the right channel."""
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_discord, fake_channel = _make_bridge()

    async def _drive():
        bridge._loop = asyncio.get_running_loop()
        msg = Message("speak", {"utterance": "hi there"}, {"channel_id": 42})
        bridge.handle_speak(msg)
        await asyncio.sleep(0.05)  # let the scheduled send run

    asyncio.run(_drive())
    fake_discord.fetch_channel.assert_called_once_with(42)
    fake_channel.send.assert_called_once_with("hi there")


def test_speak_with_no_channel_id_is_ignored():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_discord, fake_channel = _make_bridge()
    msg = Message("speak", {"utterance": "hi"}, {})
    bridge.handle_speak(msg)
    fake_channel.send.assert_not_called()


def test_speak_before_loop_running_does_not_raise():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_discord, fake_channel = _make_bridge()
    msg = Message("speak", {"utterance": "hi"}, {"channel_id": 42})
    # bridge._loop is None: discord client never started
    bridge.handle_speak(msg)
    fake_channel.send.assert_not_called()


def test_intent_failure_speaks_fallback():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_discord, fake_channel = _make_bridge()

    async def _drive():
        bridge._loop = asyncio.get_running_loop()
        msg = Message("hive.complete_intent_failure", {}, {"channel_id": 42})
        bridge.handle_intent_failure(msg)
        await asyncio.sleep(0.05)  # let the scheduled send run

    asyncio.run(_drive())
    fake_channel.send.assert_called_once()
    (text,), _ = fake_channel.send.call_args
    assert "don't know" in text


def _bridge_on_a_running_loop():
    """A bridge whose Discord loop runs in its own thread, as in production."""
    import threading

    bridge, fake_client, fake_discord, fake_channel = _make_bridge()
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    bridge._loop = loop
    return bridge, fake_discord, fake_channel, loop


def test_speak_returns_without_waiting_for_discord():
    """The hub's bus thread hands the reply over and goes back to the hub."""
    import time

    from ovos_bus_client.message import Message

    bridge, fake_discord, fake_channel, loop = _bridge_on_a_running_loop()

    async def slow_send(*args, **kwargs):
        await asyncio.sleep(0.5)

    fake_channel.send = AsyncMock(side_effect=slow_send)
    try:
        started = time.monotonic()
        bridge.handle_speak(Message("speak", {"utterance": "hi"}, {"channel_id": 42}))
        assert time.monotonic() - started < 0.1
        deadline = time.monotonic() + 2
        while not fake_channel.send.await_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fake_channel.send.await_count == 1
        assert fake_channel.send.call_args.args[0] == "hi"
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_replies_to_one_channel_keep_their_order():
    """A slow first send must not let the second reply overtake it."""
    import time

    from ovos_bus_client.message import Message

    bridge, fake_discord, fake_channel, loop = _bridge_on_a_running_loop()
    events = []
    channels = {}

    def channel_for(channel_id):
        if channel_id not in channels:
            channel = MagicMock()

            async def send(text, **kwargs):
                events.append(("start", channel_id, text))
                await asyncio.sleep(0.2 if text == "first" else 0)
                events.append(("end", channel_id, text))

            channel.send = AsyncMock(side_effect=send)
            channels[channel_id] = channel
        return channels[channel_id]

    fake_discord.get_channel = MagicMock(side_effect=channel_for)
    try:
        for text in ("first", "second"):
            bridge.handle_speak(Message("speak", {"utterance": text}, {"channel_id": 42}))
        bridge.handle_speak(Message("speak", {"utterance": "other"}, {"channel_id": 7}))
        deadline = time.monotonic() + 2
        while len(events) < 6 and time.monotonic() < deadline:
            time.sleep(0.01)
        in_channel = [event for event in events if event[1] == 42]
        assert in_channel == [("start", 42, "first"), ("end", 42, "first"),
                              ("start", 42, "second"), ("end", 42, "second")]
        # another channel did not wait behind the slow send
        assert events.index(("end", 7, "other")) < events.index(("end", 42, "first"))
        assert not bridge._channel_sends, "per-channel send state must not pile up"
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_a_failed_hub_send_never_logs_the_text(monkeypatch):
    """emit() raises with the serialized message in its text when the hub is down."""
    import hivemind_discord_bridge as bridge_module

    lines = []
    log = MagicMock()
    for level in ("debug", "info", "warning", "error", "exception"):
        getattr(log, level).side_effect = lambda *a, **k: lines.append(" ".join(map(str, a)))
    monkeypatch.setattr(bridge_module, "LOG", log)

    bridge, fake_client, fake_discord, _ = _make_bridge()
    bridge.connect_hivemind()
    fake_client.emit.side_effect = RuntimeError("Failed to emit : {'utterances': ['my secret']}")
    asyncio.run(bridge.on_message(_fake_message(text="my secret", is_dm=True)))
    assert any("RuntimeError" in line for line in lines)
    assert "my secret" not in "\n".join(lines)
