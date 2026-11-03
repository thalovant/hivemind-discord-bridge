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


def _in_channel(bridge, channel, author_id, text="<@999> what time is it"):
    return bridge.loop.dispatch(text_message(text, channel, author_id=author_id,
                                             mentions=[bridge.fake.user]))


def test_a_late_answer_to_an_expired_question_leaves_other_waits_alone(running):
    """Person scope: Alice's question timed out; its late answer must not end Bob's wait."""
    bridge = running(reply_timeout=0.4, session_scope="person")
    channel = bridge.fake.channel(500, dm=False)
    bridge.channels[500] = channel
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _, alice = bridge.hub.emitted[-1]
    assert wait_for(lambda: bridge.sent(500), timeout=2)  # Alice's wait ended
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    bridge.hub.send("speak", {"utterance": "late for alice"}, dict(alice.context))
    assert wait_for(lambda: "late for alice" in bridge.sent(500))
    time.sleep(0.05)
    assert any(key[0] == 500 for key in bridge.bridge._waits), "Bob's wait was ended"


def test_each_person_in_a_shared_channel_is_told_when_the_hub_is_down(running):
    """Channel scope: one session for the channel, but the line is throttled per person."""
    hub = FakeHub()

    def down(_):
        raise RuntimeError("Can not send messages before opening the websocket connection")

    hub.emit = down
    bridge = running(hub, reply_timeout=30)
    channel = bridge.fake.channel(600, dm=False)
    bridge.channels[600] = channel
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.sent(600)) == 1, timeout=2)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.sent(600)) == 2, timeout=2)
    _in_channel(bridge, channel, author_id=1, text="<@999> again")
    time.sleep(0.3)
    assert len(bridge.sent(600)) == 2, "the same person was told twice within 30 s"


@pytest.mark.parametrize("setting", [{"reply_timeout": float("inf")}, {"reply_timeout": float("nan")},
                                     {"reply_timeout": -1}, {"max_message_age": -1},
                                     {"max_message_age": float("nan")}])
def test_nonsense_timings_are_refused(setting):
    with pytest.raises(ValueError):
        HiveMindDiscordBridge(client=FakeHub(), discord_client=FakeDiscord(), **setting)


def test_stopping_lets_a_reply_being_sent_finish(running):
    """A long reply is several sends; the client closes only after they are done."""
    hub = FakeHub(answer=lambda message: "\n".join("x" * 1900 for _ in range(3)))
    bridge = running(hub, reply_timeout=0)
    channel = bridge.fake.channel(700, latency=0.3)
    bridge.channels[700] = channel
    bridge.loop.dispatch(text_message("tell me", channel))
    assert wait_for(lambda: channel.sent, timeout=5)
    bridge.bridge.stop()
    assert wait_for(lambda: bridge.fake.closed_at is not None, timeout=5)
    assert len(channel.done) == 3
    assert bridge.fake.closed_at >= channel.done[-1][0]


def test_a_question_that_waited_past_the_timeout_for_the_hub_is_not_sent(running):
    """Told "cannot answer" already, the person must not get an answer after that."""
    hub = FakeHub(emit_delay=0.6)
    bridge = running(hub, reply_timeout=0.3)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: bridge.sent(2), timeout=3)  # told at the timeout
    time.sleep(1.0)
    assert [message.context["channel_id"] for _, message in hub.emitted] == [1]


def test_a_hub_that_does_not_keep_up_gets_no_endless_queue(running, monkeypatch):
    monkeypatch.setattr(bridge_module, "HUB_BACKLOG_MAX", 1)
    hub = FakeHub(emit_delay=0.5)
    bridge = running(hub, reply_timeout=30)
    bridge.ask(channel_id=1, author_id=1)
    asked = bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: bridge.sent(2), timeout=2)
    assert bridge.channels[2].sent[0][0] - asked < 0.3
    assert bridge.sent(2) == [LINES["en"][UNAVAILABLE]]
    time.sleep(0.8)
    assert [message.context["channel_id"] for _, message in hub.emitted] == [1]


def test_a_refusal_is_not_pinned_on_a_wait_shared_by_several_questions(running):
    """Channel scope: two people's questions share one wait; the refused one is unknown."""
    bridge = running(reply_timeout=30)
    channel = bridge.fake.channel(800, dm=False)
    bridge.channels[800] = channel
    _in_channel(bridge, channel, author_id=1)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    bridge.hub.send("hive.policy.denied", {"denied_type": "recognizer_loop:utterance"}, {})
    time.sleep(0.3)
    assert bridge.sent(800) == []
    assert len(bridge.bridge._waits) == 1
