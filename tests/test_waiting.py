"""While the hub thinks, the channel shows "typing…"; if no answer comes, the person is told.

"typing…" starts as soon as the question arrives, is renewed while the hub
thinks, and stops on the answer. With no answer within the reply timeout,
the bridge says it cannot answer right now, in HIVEMIND_LANG; so it does at
once when the hub cannot be reached, or refuses the only question waiting.
A late answer is still delivered.
"""
import time

import pytest

import hivemind_discord_bridge as bridge_module
from hivemind_discord_bridge import HiveMindDiscordBridge
from hivemind_discord_bridge.lines import LINES, UNAVAILABLE

from conftest import DiscordLoop, FakeChannel, FakeDiscord, FakeHub, text_message, wait_for


@pytest.fixture(autouse=True)
def quick_typing(monkeypatch):
    monkeypatch.setattr(bridge_module, "TYPING_EVERY", 0.2)


class Running:
    def __init__(self, hub=None, **kwargs):
        self.hub = hub or FakeHub()
        self.fake = FakeDiscord()
        self.bridge = HiveMindDiscordBridge(client=self.hub, discord_client=self.fake, **kwargs)
        self.bridge.connect_hivemind()
        self.loop = DiscordLoop(self.bridge)
        self.channels = {}

    def ask(self, channel_id=42, author_id=1, registered=True):
        channel = self.channels.get(channel_id)
        if channel is None:
            channel = self.fake.channel(channel_id) if registered else None
            if channel is None:  # a channel discord.py has not cached
                channel = FakeChannel(channel_id)
            self.channels[channel_id] = channel
        return self.loop.dispatch(text_message("what time is it", channel, author_id=author_id))

    def sent(self, channel_id=42):
        return [content for _, content, _ in self.channels[channel_id].sent]

    def typed(self, channel_id=42):
        return self.channels[channel_id].typed

    def stop(self):
        self.loop.stop()
        self.hub.close()


@pytest.fixture
def running():
    made = []

    def start(hub=None, **kwargs):
        made.append(Running(hub, **kwargs))
        return made[-1]

    yield start
    for each in made:
        each.stop()


def _answer_last(hub, text="it is noon"):
    _, message = hub.emitted[-1]
    hub.send("speak", {"utterance": text}, dict(message.context))


def test_typing_shows_at_once_is_renewed_and_stops_on_the_answer(running):
    bridge = running()
    asked = bridge.ask()
    assert wait_for(lambda: bridge.typed())
    assert bridge.typed()[0] - asked < 0.1, "typing… came late"
    assert wait_for(lambda: len(bridge.typed()) >= 3, timeout=3), "typing… was not renewed"
    assert wait_for(lambda: bridge.hub.emitted)
    _answer_last(bridge.hub)
    assert wait_for(lambda: bridge.sent() == ["it is noon"])
    count = len(bridge.typed())
    time.sleep(0.6)
    assert len(bridge.typed()) == count, "typing… went on after the answer"
    assert not bridge.bridge._waits


def test_no_answer_in_time_is_said_and_a_late_answer_still_comes(running):
    bridge = running(reply_timeout=0.5, lang="fr-fr")
    bridge.ask()
    assert wait_for(lambda: bridge.sent(), timeout=3)
    assert bridge.sent() == [LINES["fr"][UNAVAILABLE]]
    count = len(bridge.typed())
    time.sleep(0.5)
    assert len(bridge.typed()) == count, "typing… went on after the timeout"
    _answer_last(bridge.hub, "late answer")
    assert wait_for(lambda: bridge.sent()[-1:] == ["late answer"])


def test_the_hub_done_without_an_answer_ends_the_wait_quietly(running):
    bridge = running(reply_timeout=0.5)
    bridge.ask()
    assert wait_for(lambda: bridge.hub.emitted)
    _, message = bridge.hub.emitted[-1]
    bridge.hub.send("ovos.utterance.handled", {}, dict(message.context))
    time.sleep(0.9)
    assert bridge.sent() == []
    assert not bridge.bridge._waits


def test_a_hub_that_cannot_be_reached_is_said_at_once(running):
    hub = FakeHub()

    def down(_):
        raise RuntimeError("Can not send messages before opening the websocket connection")

    hub.emit = down
    bridge = running(hub, reply_timeout=30)
    asked = bridge.ask()
    assert wait_for(lambda: bridge.sent(), timeout=2)
    assert bridge.sent() == [LINES["en"][UNAVAILABLE]]
    assert bridge.channels[42].sent[0][0] - asked < 0.5


def test_a_refusal_of_the_only_waiting_question_is_said_at_once(running):
    bridge = running(reply_timeout=30)
    bridge.ask()
    assert wait_for(lambda: bridge.hub.emitted)
    bridge.hub.send("hive.policy.denied", {"denied_type": "recognizer_loop:utterance",
                                           "code": "intent_quota_exceeded"}, {})
    assert wait_for(lambda: bridge.sent() == [LINES["en"][UNAVAILABLE]], timeout=2)
    assert not bridge.bridge._waits


def test_a_refusal_that_could_be_anyones_is_left_to_the_timeout(running):
    bridge = running(reply_timeout=30)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    bridge.hub.send("hive.policy.denied", {"denied_type": "recognizer_loop:utterance"}, {})
    time.sleep(0.3)
    assert bridge.sent(1) == [] and bridge.sent(2) == []
    assert len(bridge.bridge._waits) == 2


def test_the_cannot_answer_line_is_not_repeated_to_the_same_person(running):
    bridge = running(reply_timeout=0.3)
    bridge.ask()
    assert wait_for(lambda: bridge.sent(), timeout=2)
    bridge.ask()
    time.sleep(0.7)
    assert bridge.sent() == [LINES["en"][UNAVAILABLE]]


def test_waiting_can_be_turned_off(running):
    bridge = running(reply_timeout=0)
    bridge.ask()
    assert wait_for(lambda: bridge.hub.emitted)
    time.sleep(0.4)
    assert bridge.typed() == [] and bridge.sent() == []


def test_a_failing_typing_trigger_changes_nothing(running):
    bridge = running()
    bridge.ask()
    bridge.channels[42].fail_typing = True
    assert wait_for(lambda: bridge.hub.emitted)
    _answer_last(bridge.hub)
    assert wait_for(lambda: bridge.sent() == ["it is noon"])


def test_a_reply_reuses_the_channel_the_question_came_from(running):
    """No fetch_channel API call per reply to a channel discord.py has not cached."""
    bridge = running(reply_timeout=0)
    bridge.ask(channel_id=77, registered=False)
    assert wait_for(lambda: bridge.hub.emitted)
    _answer_last(bridge.hub)
    assert wait_for(lambda: bridge.sent(77) == ["it is noon"])
    assert bridge.fake.fetched == []
