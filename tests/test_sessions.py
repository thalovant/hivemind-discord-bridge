"""Who shares a conversation on the hub.

With one session per channel, Bob's "and tomorrow?" continues Alice's
question about the weather. ``session_scope="person"`` gives every person
their own conversation in each channel; ``"channel"`` keeps the channel as one.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

BOT_ID = 999


def bridge(**kwargs):
    from hivemind_discord_bridge import HiveMindDiscordBridge

    client = MagicMock(name="HiveMessageBusClient")
    fake_discord = MagicMock(name="discord.Client")
    fake_discord.event = MagicMock(side_effect=lambda f: f)
    fake_discord.user.id = BOT_ID
    fake_discord.fetch_channel = AsyncMock()
    made = HiveMindDiscordBridge(client=client, discord_client=fake_discord, **kwargs)
    made.connect_hivemind()
    return made, client


def session_of(made, client, user_id, channel_id=42):
    msg = MagicMock()
    msg.content = f"<@{BOT_ID}> what is the weather"
    msg.author.id = user_id
    msg.author.name = f"user{user_id}"
    msg.channel = MagicMock(spec=discord.TextChannel)
    msg.channel.id = channel_id
    msg.mentions = []
    msg.reference = None
    client.emit.reset_mock()
    asyncio.run(made.on_message(msg))
    return client.emit.call_args[0][0].payload.context["session"]["session_id"]


def test_by_default_a_channel_is_one_conversation():
    made, client = bridge()
    assert session_of(made, client, 1) == session_of(made, client, 2) == "discord-42"


def test_per_person_sessions_keep_each_person_apart():
    made, client = bridge(session_scope="person")
    alice, bob = session_of(made, client, 1), session_of(made, client, 2)
    assert alice != bob
    assert alice == "discord-42-1"
    assert session_of(made, client, 1, channel_id=43) != alice


def test_an_unknown_scope_is_refused():
    with pytest.raises(ValueError):
        bridge(session_scope="room")
