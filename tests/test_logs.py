"""Nothing anyone wrote or was told ends up in the bridge's log, at any level.

A debug log is shipped and read far more widely than the chat it came from.
"""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import discord

import hivemind_discord_bridge as bridge_module

SECRET_QUESTION = "is my biopsy result back"
SECRET_ANSWER = "Your appointment is on Tuesday"


def test_neither_questions_nor_answers_are_logged(monkeypatch):
    lines = []
    log = MagicMock()
    for level in ("debug", "info", "warning", "error", "exception"):
        getattr(log, level).side_effect = lambda *a, **k: lines.append(" ".join(map(str, a)))
    monkeypatch.setattr(bridge_module, "LOG", log)

    client = MagicMock()
    fake_discord = MagicMock()
    fake_discord.event = MagicMock(side_effect=lambda f: f)
    fake_discord.user.id = 999
    fake_discord.get_channel = MagicMock(return_value=None)
    channel = MagicMock()
    channel.send = AsyncMock()
    fake_discord.fetch_channel = AsyncMock(return_value=channel)
    made = bridge_module.HiveMindDiscordBridge(client=client, discord_client=fake_discord)
    made.connect_hivemind()

    msg = MagicMock()
    msg.content = SECRET_QUESTION
    msg.author.id = 1
    msg.channel = MagicMock(spec=discord.DMChannel)
    msg.channel.id = 42
    msg.mentions = []
    msg.reference = None
    asyncio.run(made.on_message(msg))

    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    try:
        made._loop = loop
        made.speak(SECRET_ANSWER, 42)
    finally:
        loop.call_soon_threadsafe(loop.stop)

    assert lines, "the bridge logged nothing; the test would prove nothing"
    joined = "\n".join(lines)
    assert SECRET_QUESTION not in joined
    assert SECRET_ANSWER not in joined
