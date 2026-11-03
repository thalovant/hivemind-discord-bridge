"""A question delivered long after it was asked is not answered.

Discord does not replay history on connect, but a gateway RESUME after an
outage delivers the events missed meanwhile. A message older than
``max_message_age`` (120 s) when it arrives is not answered.
"""
import asyncio
from unittest.mock import MagicMock

from hivemind_discord_bridge import MAX_MESSAGE_AGE, HiveMindDiscordBridge

from conftest import FakeDiscord, text_message


def _bridge(**kwargs):
    client = MagicMock()
    fake = FakeDiscord()
    made = HiveMindDiscordBridge(client=client, discord_client=fake, **kwargs)
    made.connect_hivemind()
    return made, client, fake


def test_the_default_age_is_two_minutes():
    assert MAX_MESSAGE_AGE == 120


def test_a_fresh_message_is_answered_and_an_old_one_is_not():
    made, client, fake = _bridge()
    channel = fake.channel(42)
    asyncio.run(made.on_message(text_message("hello", channel, age=5)))
    assert client.emit.call_count == 1
    asyncio.run(made.on_message(text_message("hello", channel, age=121)))
    assert client.emit.call_count == 1


def test_the_age_can_be_changed():
    made, client, fake = _bridge(max_message_age=600)
    asyncio.run(made.on_message(text_message("hello", fake.channel(42), age=300)))
    assert client.emit.call_count == 1
