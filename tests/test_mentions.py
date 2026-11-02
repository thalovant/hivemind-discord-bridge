"""In a server channel the bot answers only what is addressed to it, and never sees its own mention.

A question in a channel reads "<@999> what time is it". The mention is how
the person addresses the bot, not part of the question. And a bot that
forwards every message asks the hub about every conversation in every
channel it can read; it also needs Discord's privileged message content
intent to do so, which a mention-only bot does not.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

BOT_ID = 999


def bridge_with_hub(**kwargs):
    from hivemind_discord_bridge import HiveMindDiscordBridge

    client = MagicMock(name="HiveMessageBusClient")
    fake_discord = MagicMock(name="discord.Client")
    fake_discord.event = MagicMock(side_effect=lambda f: f)
    fake_discord.user.id = BOT_ID
    fake_discord.fetch_channel = AsyncMock()
    made = HiveMindDiscordBridge(client=client, discord_client=fake_discord, **kwargs)
    made.connect_hivemind()
    return made, client


def message(text, dm=False, mentions_bot=None, reply_to_bot=False):
    msg = MagicMock()
    msg.content = text
    msg.author.id = 1
    msg.author.name = "alice"
    msg.channel = MagicMock(spec=discord.DMChannel if dm else discord.TextChannel)
    msg.channel.id = 42
    if mentions_bot is None:
        mentions_bot = f"<@{BOT_ID}>" in text or f"<@!{BOT_ID}>" in text
    bot = MagicMock()
    bot.id = BOT_ID
    msg.mentions = [bot] if mentions_bot else []
    if reply_to_bot:
        msg.reference.resolved.author.id = BOT_ID
    else:
        msg.reference = None
    return msg


def forwarded(made, client, msg):
    client.emit.reset_mock()
    asyncio.run(made.on_message(msg))
    if not client.emit.called:
        return None
    return client.emit.call_args[0][0].payload.data["utterances"][0]


def test_the_mention_is_removed_from_the_question():
    made, client = bridge_with_hub()
    assert forwarded(made, client, message(f"<@{BOT_ID}> what time is it")) == "what time is it"
    assert forwarded(made, client, message(f"what time is it <@!{BOT_ID}>")) == "what time is it"


def test_a_channel_message_not_addressed_to_the_bot_is_not_forwarded():
    made, client = bridge_with_hub()
    assert forwarded(made, client, message("lunch at noon?")) is None


def test_another_users_mention_is_not_the_bots():
    made, client = bridge_with_hub()
    assert forwarded(made, client, message("<@12345> lunch at noon?")) is None


def test_a_reply_to_the_bot_is_addressed_to_it():
    made, client = bridge_with_hub()
    assert forwarded(made, client, message("and tomorrow?", reply_to_bot=True)) == "and tomorrow?"


def test_a_direct_message_needs_no_mention():
    made, client = bridge_with_hub()
    assert forwarded(made, client, message("what time is it", dm=True)) == "what time is it"


def test_a_message_that_is_only_the_mention_is_not_forwarded():
    made, client = bridge_with_hub()
    assert forwarded(made, client, message(f"<@{BOT_ID}>")) is None


def test_gating_can_be_turned_off():
    made, client = bridge_with_hub(require_mention=False)
    assert forwarded(made, client, message("lunch at noon?")) == "lunch at noon?"


def test_a_mention_only_bot_does_not_ask_for_the_privileged_intent():
    from hivemind_discord_bridge import HiveMindDiscordBridge

    gated = HiveMindDiscordBridge(token="t", client=MagicMock())
    assert gated.discord.intents.message_content is False
    reading_all = HiveMindDiscordBridge(token="t", client=MagicMock(), require_mention=False)
    assert reading_all.discord.intents.message_content is True
