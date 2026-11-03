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


def waiting(made, channel_id=None):
    """How many questions the bridge is waiting on (in ``channel_id``, or anywhere)."""
    return sum(len(queue) for (channel, _), queue in list(made._pending.items())
               if channel_id is None or channel == channel_id)


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
    assert waiting(bridge.bridge) == 0


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
    assert waiting(bridge.bridge) == 0


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
    assert waiting(bridge.bridge) == 0


def test_a_refusal_that_could_be_anyones_is_left_to_the_timeout(running):
    bridge = running(reply_timeout=30)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    bridge.hub.send("hive.policy.denied", {"denied_type": "recognizer_loop:utterance"}, {})
    time.sleep(0.3)
    assert bridge.sent(1) == [] and bridge.sent(2) == []
    assert waiting(bridge.bridge) == 2


def test_each_unanswered_question_gets_its_own_line(running):
    """One person asks twice; neither is answered: two lines, each at its own timeout."""
    bridge = running(reply_timeout=0.4)
    first = bridge.ask()
    time.sleep(0.2)
    second = bridge.ask()
    assert wait_for(lambda: len(bridge.sent()) == 2, timeout=3)
    assert bridge.sent() == [LINES["en"][UNAVAILABLE]] * 2
    sends = [t for t, _, _ in bridge.channels[42].sent]
    assert sends[0] - first >= 0.35 and sends[1] - second >= 0.35


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
    assert waiting(bridge.bridge, 500) == 1, "Bob's wait was ended"


def test_each_question_in_a_shared_channel_is_told_when_the_hub_is_down(running):
    """Channel scope: one session for the channel, one line per question that cannot go."""
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
    assert wait_for(lambda: len(bridge.sent(600)) == 3, timeout=2)


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


def test_a_refusal_is_not_pinned_on_one_of_several_waiting_questions(running):
    """Channel scope: two people's questions wait; the refused one is unknown."""
    bridge = running(reply_timeout=30)
    channel = bridge.fake.channel(800, dm=False)
    bridge.channels[800] = channel
    _in_channel(bridge, channel, author_id=1)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    bridge.hub.send("hive.policy.denied", {"denied_type": "recognizer_loop:utterance"}, {})
    time.sleep(0.3)
    assert bridge.sent(800) == []
    assert waiting(bridge.bridge, 800) == 2


def _shared(bridge, channel_id):
    channel = bridge.fake.channel(channel_id, dm=False)
    bridge.channels[channel_id] = channel
    return channel


def test_two_people_in_one_channel_each_keep_their_wait(running):
    """Channel scope: one session; the hub answers Alice; Bob still waits, then hears his own line."""
    bridge = running(reply_timeout=0.8)
    channel = _shared(bridge, 900)
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    _, alice = bridge.hub.emitted[0]
    bridge.hub.send("speak", {"utterance": "noon, Alice"}, dict(alice.context))
    bridge.hub.send("ovos.utterance.handled", {}, dict(alice.context))
    assert wait_for(lambda: "noon, Alice" in bridge.sent(900))
    time.sleep(0.1)
    assert waiting(bridge.bridge, 900) == 1, "Bob's wait ended with Alice's answer"
    count = len(bridge.typed(900))
    assert wait_for(lambda: len(bridge.typed(900)) > count, timeout=1), "no typing… for Bob"
    assert wait_for(lambda: bridge.sent(900)[-1:] == [LINES["en"][UNAVAILABLE]], timeout=3)
    assert bridge.sent(900) == ["noon, Alice", LINES["en"][UNAVAILABLE]]


def test_one_person_asking_twice_gets_one_wait_per_question(running):
    """The first reply ends the first question's wait only; the second is answered next."""
    bridge = running(reply_timeout=30, session_scope="person")
    channel = _shared(bridge, 901)
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _in_channel(bridge, channel, author_id=1, text="<@999> and the weather")
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    _, first = bridge.hub.emitted[0]
    _, second = bridge.hub.emitted[1]
    bridge.hub.send("speak", {"utterance": "noon"}, dict(first.context))
    bridge.hub.send("ovos.utterance.handled", {}, dict(first.context))
    assert wait_for(lambda: bridge.sent(901) == ["noon"])
    time.sleep(0.1)
    assert waiting(bridge.bridge, 901) == 1
    bridge.hub.send("speak", {"utterance": "sunny"}, dict(second.context))
    assert wait_for(lambda: bridge.sent(901) == ["noon", "sunny"])
    time.sleep(0.05)
    assert waiting(bridge.bridge) == 0


def test_a_second_sentence_of_an_answer_ends_no_other_wait(running):
    """Person scope: Alice's answer comes in two speaks; Bob's wait is untouched."""
    bridge = running(reply_timeout=30, session_scope="person")
    channel = _shared(bridge, 902)
    _in_channel(bridge, channel, author_id=1)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    alice = next(m for _, m in bridge.hub.emitted if m.context["user"]["discord_user_id"] == 1)
    for sentence in ("It is noon.", "Have a nice lunch."):
        bridge.hub.send("speak", {"utterance": sentence}, dict(alice.context))
    bridge.hub.send("ovos.utterance.handled", {}, dict(alice.context))
    assert wait_for(lambda: len(bridge.sent(902)) == 2)
    time.sleep(0.1)
    assert waiting(bridge.bridge, 902) == 1


def test_an_answer_with_no_session_ends_only_the_oldest_wait(running):
    """Person scope: an answer that names no session ends Alice's wait, not Bob's."""
    bridge = running(reply_timeout=30, session_scope="person")
    channel = _shared(bridge, 903)
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    _, alice = bridge.hub.emitted[0]
    context = {k: v for k, v in alice.context.items() if k != "session"}
    bridge.hub.send("speak", {"utterance": "noon"}, context)
    assert wait_for(lambda: bridge.sent(903) == ["noon"])
    time.sleep(0.1)
    left = [q.author_id for queue in bridge.bridge._pending.values() for q in queue]
    assert left == [2]


def test_in_a_shared_channel_a_second_sentence_ends_no_other_wait(running):
    """Channel scope (one session): Alice's answer in two speaks leaves Bob's wait alone."""
    bridge = running(reply_timeout=30)
    channel = _shared(bridge, 910)
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    _, alice = bridge.hub.emitted[0]
    for sentence in ("It is noon.", "Have a nice lunch."):
        bridge.hub.send("speak", {"utterance": sentence}, dict(alice.context))
    bridge.hub.send("ovos.utterance.handled", {}, dict(alice.context))
    assert wait_for(lambda: len(bridge.sent(910)) == 2)
    time.sleep(0.1)
    left = [q.author_id for queue in bridge.bridge._pending.values() for q in queue]
    assert left == [2], "Bob's wait was ended by Alice's second sentence"


def test_an_answer_for_a_session_with_nothing_waiting_ends_no_wait(running):
    """However late (the session long forgotten), it never ends someone else's wait."""
    bridge = running(reply_timeout=30, session_scope="person")
    channel = _shared(bridge, 911)
    _in_channel(bridge, channel, author_id=2)
    assert wait_for(lambda: bridge.hub.emitted)
    bridge.hub.send("speak", {"utterance": "very late, for someone"},
                    {"channel_id": 911, "session": {"session_id": "discord-911-7"}})
    assert wait_for(lambda: bridge.sent(911) == ["very late, for someone"])
    time.sleep(0.05)
    assert waiting(bridge.bridge, 911) == 1


def test_a_refusal_long_after_the_only_question_is_not_pinned_on_it(running, monkeypatch):
    """Refusals come at admission: one arriving later is not about this question."""
    monkeypatch.setattr(bridge_module, "REFUSAL_WINDOW", 0.2)
    bridge = running(reply_timeout=30)
    bridge.ask()
    assert wait_for(lambda: bridge.hub.emitted)
    time.sleep(0.4)
    bridge.hub.send("hive.policy.denied", {"denied_type": "recognizer_loop:utterance"}, {})
    time.sleep(0.3)
    assert bridge.sent() == []
    assert waiting(bridge.bridge) == 1


def test_a_question_that_got_too_old_in_the_hub_queue_is_not_sent(running):
    """Fresh when it arrived, but older than max_message_age once the hub socket is free."""
    hub = FakeHub(emit_delay=0.8)
    bridge = running(hub, reply_timeout=30, max_message_age=0.5)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: len(hub.emitted) == 1, timeout=3)
    time.sleep(1.0)  # long enough for the second to have been sent, had it gone
    assert [message.context["channel_id"] for _, message in hub.emitted] == [1]


def test_replies_queued_for_one_channel_are_capped(running, monkeypatch):
    monkeypatch.setattr(bridge_module, "SENDS_PER_CHANNEL_MAX", 2)
    bridge = running(reply_timeout=0)
    channel = bridge.fake.channel(920, latency=0.3)
    bridge.channels[920] = channel
    for n in range(5):
        bridge.bridge.speak(f"reply {n}", 920)
    time.sleep(1.0)
    assert bridge.sent(920) == ["reply 0", "reply 1"]


def test_a_late_answer_ends_only_its_own_question(running):
    """The hub echoes the question number: Alice's late answer leaves her newer question waiting."""
    bridge = running(reply_timeout=0.4, session_scope="person")
    channel = _shared(bridge, 930)
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _, first = bridge.hub.emitted[0]
    assert wait_for(lambda: bridge.sent(930), timeout=2)  # the first one timed out
    bridge.bridge.reply_timeout = 30
    _in_channel(bridge, channel, author_id=1, text="<@999> and the weather")
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    bridge.hub.send("speak", {"utterance": "late: noon"}, dict(first.context))
    assert wait_for(lambda: "late: noon" in bridge.sent(930))
    time.sleep(0.05)
    assert waiting(bridge.bridge, 930) == 1, "the newer question's wait was ended"


def test_a_second_sentence_never_ends_the_same_persons_next_question(running):
    """Two questions from Alice; her first answer in two speaks; the second still waits."""
    bridge = running(reply_timeout=30, session_scope="person")
    channel = _shared(bridge, 931)
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: len(bridge.hub.emitted) == 1)
    _in_channel(bridge, channel, author_id=1, text="<@999> and the weather")
    assert wait_for(lambda: len(bridge.hub.emitted) == 2)
    _, first = bridge.hub.emitted[0]
    for sentence in ("It is noon.", "Have a nice lunch."):
        bridge.hub.send("speak", {"utterance": sentence}, dict(first.context))
    bridge.hub.send("ovos.utterance.handled", {}, dict(first.context))
    assert wait_for(lambda: len(bridge.sent(931)) == 2)
    time.sleep(0.1)
    assert waiting(bridge.bridge, 931) == 1


def test_a_refusal_counts_from_the_send_not_from_the_queue(running, monkeypatch):
    """The second question waits 0.6 s for the hub socket; its refusal still finds it."""
    monkeypatch.setattr(bridge_module, "REFUSAL_WINDOW", 0.3)
    hub = FakeHub(emit_delay=lambda m: 0.6 if m.context["channel_id"] == 1 else 0,
                  refuse=lambda m: m.context["channel_id"] == 2)
    bridge = running(hub, reply_timeout=30)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: bridge.sent(2) == [LINES["en"][UNAVAILABLE]], timeout=3)


def test_a_question_dropped_as_too_old_is_told_at_once(running):
    hub = FakeHub(emit_delay=0.8)
    bridge = running(hub, reply_timeout=30, max_message_age=0.5)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    assert wait_for(lambda: bridge.sent(2), timeout=3)
    assert bridge.sent(2) == [LINES["en"][UNAVAILABLE]]
    assert waiting(bridge.bridge, 2) == 0


def test_a_reply_dropped_by_the_channel_cap_is_followed_by_the_line(running, monkeypatch):
    """The cap drops the reply, not the person: the line has room past the cap."""
    monkeypatch.setattr(bridge_module, "SENDS_PER_CHANNEL_MAX", 1)
    bridge = running(reply_timeout=30, session_scope="person")
    channel = bridge.fake.channel(940, dm=False, latency=0.5)
    bridge.channels[940] = channel
    _in_channel(bridge, channel, author_id=1)
    assert wait_for(lambda: bridge.hub.emitted)
    _, question = bridge.hub.emitted[0]
    bridge.bridge.speak("busy", 940, "another-session")  # fills the channel's queue
    assert wait_for(lambda: channel.sent)
    bridge.hub.send("speak", {"utterance": "noon"}, dict(question.context))
    assert wait_for(lambda: len(bridge.sent(940)) == 2, timeout=3)
    assert bridge.sent(940) == ["busy", LINES["en"][UNAVAILABLE]]


def test_stopping_sends_no_question_still_queued_for_the_hub(running):
    hub = FakeHub(emit_delay=0.5)
    bridge = running(hub, reply_timeout=30)
    bridge.ask(channel_id=1, author_id=1)
    bridge.ask(channel_id=2, author_id=2)
    time.sleep(0.15)  # the first is being sent, the second waits for the thread
    bridge.bridge.stop()
    time.sleep(1.0)
    assert [message.context["channel_id"] for _, message in hub.emitted] == [1]


def test_a_whitespace_only_answer_is_not_a_reply(running):
    bridge = running(reply_timeout=30)
    bridge.ask()
    assert wait_for(lambda: bridge.hub.emitted)
    _, message = bridge.hub.emitted[0]
    bridge.hub.send("speak", {"utterance": "   \n "}, dict(message.context))
    time.sleep(0.3)
    assert bridge.sent() == []
    assert waiting(bridge.bridge) == 1, "an answer nobody could see ended the wait"
