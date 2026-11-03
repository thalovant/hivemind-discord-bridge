"""Neither side waits for the other: measured with a fake hub and fake Discord channels.

The bridge sits between two networks that each have their own thread: the
asyncio loop discord.py runs on, and the HiveMind client's bus thread that
delivers the hub's messages one after the other. A reply that waits for
Discord on the bus thread holds up every later hub message (and the
client's keepalive); a hub send that runs on the loop holds up every reply
and the gateway heartbeat.

Two scenarios, with messages dispatched to the bridge as discord.py does:

- burst: five people write at once; the hub answers each 50 ms later;
  Discord takes 300 ms per message sent.
- slow hub socket: two messages; each hub send takes 250 ms; the first
  answer arrives while the second message is being sent.

Numbers are printed (``pytest -s``) and appended as JSON lines to
``$LATENCY_REPORT`` when that is set.
"""
import json
import os
import statistics

from hivemind_discord_bridge import HiveMindDiscordBridge

from conftest import DiscordLoop, FakeDiscord, FakeHub, text_message, wait_for

#: generous enough for a loaded CI runner, far below the blocking it guards
#: against (300 ms per queued reply, 250 ms per hub send)
LIMIT = 0.1


def _report(name, numbers):
    line = {"scenario": name, **{k: round(v * 1000, 1) for k, v in numbers.items()}}
    print(f"\nlatency[{name}] " + " ".join(f"{k}={v}ms" for k, v in line.items() if k != "scenario"))
    path = os.environ.get("LATENCY_REPORT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")


def _bridge(hub):
    fake = FakeDiscord()
    bridge = HiveMindDiscordBridge(client=hub, discord_client=fake)
    bridge.connect_hivemind()
    return bridge, fake, DiscordLoop(bridge)


def test_burst_replies_do_not_queue_behind_each_other():
    hub = FakeHub(answer=lambda message: "the answer", think=0.05)
    bridge, fake, loop = _bridge(hub)
    channels = [fake.channel(cid, latency=0.3) for cid in (101, 102, 103, 104, 105)]
    try:
        dispatched = {c.id: loop.dispatch(text_message("what time is it", c, author_id=c.id))
                      for c in channels}
        # answered, not just started: a send cancelled half way must fail this
        assert wait_for(lambda: all(c.done for c in channels), timeout=15), "not every reply was sent"
    finally:
        loop.stop()
        hub.close()
    inbound = [at - dispatched[m.context["channel_id"]] for at, m in hub.emitted]
    outbound = [fake.channels[cid].sent[0][0] - sent_at for sent_at, cid in hub.replies]
    numbers = {"inbound_median": statistics.median(inbound), "inbound_max": max(inbound),
               "outbound_median": statistics.median(outbound), "outbound_max": max(outbound),
               "bus_held_max": max(hub.held)}
    typing = [c.typed[0] - dispatched[c.id] for c in channels if c.typed]
    if typing:  # "typing…" shown after the message arrived
        numbers["typing_median"], numbers["typing_max"] = statistics.median(typing), max(typing)
    _report("burst", numbers)
    assert numbers["inbound_max"] < LIMIT
    assert numbers["outbound_max"] < LIMIT, "replies waited for each other's send"
    assert numbers["bus_held_max"] < LIMIT, "a reply held the hub's bus thread"


def test_a_slow_hub_send_does_not_hold_up_replies():
    hub = FakeHub(answer=lambda message: "the answer", think=0.01, emit_delay=0.25)
    bridge, fake, loop = _bridge(hub)
    first, second = fake.channel(201), fake.channel(202)
    try:
        loop.dispatch(text_message("first", first, author_id=1))
        loop.dispatch(text_message("second", second, author_id=2))
        assert wait_for(lambda: first.done and second.done, timeout=15)
    finally:
        loop.stop()
        hub.close()
    (first_at, first_cid), (second_at, second_cid) = hub.replies[:2]
    numbers = {"outbound_first": fake.channels[first_cid].sent[0][0] - first_at,
               "outbound_second": fake.channels[second_cid].sent[0][0] - second_at,
               "bus_held_max": max(hub.held)}
    _report("slow-hub-socket", numbers)
    assert numbers["outbound_first"] < LIMIT, "a reply waited for a hub send on the loop"
    assert numbers["outbound_second"] < LIMIT, "the second reply waited"
