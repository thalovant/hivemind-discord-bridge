"""A long reply is sent as several Discord messages, unchanged, and never pings anyone.

Discord refuses a message over 2000 characters, and a long answer used to
be lost whole. And a reply that echoes "@everyone" must not ping the
server: the hub's text comes from what people wrote.
"""
import pytest

from hivemind_discord_bridge import HiveMindDiscordBridge
from hivemind_discord_bridge.split import DISCORD_LIMIT, split_message

from conftest import DiscordLoop, FakeDiscord, FakeHub, text_message, wait_for


def units(text):
    return len(text.encode("utf-16-le")) // 2


def test_a_short_reply_is_one_message():
    assert split_message("it is noon") == ["it is noon"]


def test_pieces_fit_and_join_back_to_the_reply():
    text = "\n\n".join(f"Paragraph {n}. " + "word " * 150 for n in range(10))
    pieces = split_message(text)
    assert len(pieces) > 1
    assert "".join(pieces) == text
    assert all(units(piece) <= DISCORD_LIMIT for piece in pieces)


def test_a_cut_falls_on_a_paragraph_before_a_word():
    first = "a " * 700
    assert split_message(first + "\n\n" + "b " * 700)[0] == first + "\n\n"


def test_an_emoji_outside_the_bmp_counts_twice():
    text = "\U0001F600" * 1500  # 3000 UTF-16 code units, no space anywhere
    pieces = split_message(text)
    assert "".join(pieces) == text
    assert [units(piece) for piece in pieces] == [2000, 1000]


def test_a_word_longer_than_a_message_is_cut():
    assert [len(piece) for piece in split_message("x" * 4500)] == [2000, 2000, 500]


def test_a_tiny_limit_is_refused():
    with pytest.raises(ValueError):
        split_message("abc", limit=1)


def _ask_and_get(answer):
    hub = FakeHub(answer=lambda message: answer)
    fake = FakeDiscord()
    channel = fake.channel(42)
    bridge = HiveMindDiscordBridge(client=hub, discord_client=fake, reply_timeout=0)
    bridge.connect_hivemind()
    loop = DiscordLoop(bridge)
    try:
        loop.dispatch(text_message("tell me", channel))
        assert wait_for(lambda: "".join(c for _, c, _ in channel.sent) == answer, timeout=5)
        return channel.sent
    finally:
        loop.stop()
        hub.close()


def test_a_long_answer_reaches_discord_in_order():
    answer = "\n".join(f"line {n:04d} " + "z" * 60 for n in range(100))
    sent = _ask_and_get(answer)
    assert len(sent) > 1
    assert all(units(content) <= DISCORD_LIMIT for _, content, _ in sent)


def test_a_reply_never_pings_anyone():
    (_, content, kwargs), = _ask_and_get("@everyone the meeting moved, <@&123> <@456>")
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert mentions.users is False and mentions.roles is False
