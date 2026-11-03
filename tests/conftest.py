"""Fakes for tests that run the bridge on a live event loop.

``FakeDiscord`` stands in for ``discord.Client`` (channels whose ``send``
and ``typing`` are timed and can be slow); ``DiscordLoop`` runs an asyncio
loop in its own thread and dispatches messages to the bridge as
discord.py does, one task per event. ``FakeHub`` stands in for
``HiveMessageBusClient``: it records what the bridge emits and delivers hub
messages on its own bus thread, one after the other, like the real
client's websocket thread.
"""
import asyncio
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from unittest.mock import MagicMock

import discord


class FakeChannel:
    """A Discord channel: ``sent`` and ``typed`` record ``(time, ...)``."""

    def __init__(self, channel_id: int, dm: bool = True, latency: float = 0.0):
        self.id = channel_id
        self.type = discord.ChannelType.private if dm else discord.ChannelType.text
        self.latency = latency
        self.sent: List[tuple] = []
        self.done: List[tuple] = []  # like sent, once Discord has answered
        self.typed: List[float] = []
        self.fail_typing = False
        self.refuse: set = set()  # contents Discord answers with an error

    async def send(self, content=None, **kwargs):
        self.sent.append((time.monotonic(), content, kwargs))
        if content in self.refuse:
            raise discord.HTTPException(MagicMock(status=403, reason="Forbidden"),
                                        {"code": 50013, "message": "Missing Permissions"})
        if self.latency:
            await asyncio.sleep(self.latency)
        self.done.append((time.monotonic(), content, kwargs))

    def typing(self):
        channel = self

        class Typing:
            def __await__(self):
                async def once():
                    channel.typed.append(time.monotonic())
                    if channel.fail_typing:
                        raise discord.HTTPException(MagicMock(status=500), "nope")
                return once().__await__()

        return Typing()


class FakeDiscord:
    """The part of ``discord.Client`` the bridge uses."""

    def __init__(self):
        self.user = MagicMock(id=999)
        self.channels: Dict[int, FakeChannel] = {}
        self.fetched: List[int] = []
        self.closed_at: Optional[float] = None

    def event(self, handler):
        return handler

    def channel(self, channel_id: int, **kwargs) -> FakeChannel:
        made = self.channels[channel_id] = FakeChannel(channel_id, **kwargs)
        return made

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        self.fetched.append(channel_id)
        return self.channels[channel_id]

    def is_ready(self):
        return True

    async def close(self):
        self.closed_at = time.monotonic()

    def is_closed(self):
        return False


def text_message(content: str, channel: FakeChannel, author_id: int = 1,
                 age: float = 0.0, mentions=()):
    message = MagicMock()
    message.content = content
    message.author.id = author_id
    message.author.name = f"user{author_id}"
    message.channel = channel
    message.mentions = list(mentions)
    message.reference = None
    message.created_at = datetime.fromtimestamp(time.time() - age, timezone.utc)
    return message


class DiscordLoop:
    """An asyncio loop in its own thread, dispatching to ``bridge.on_message``."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="discord-loop", daemon=True)
        self.thread.start()
        bridge._loop = self.loop

    def dispatch(self, message) -> float:
        """Hand ``message`` to the bridge as discord.py would; returns when."""
        at = time.monotonic()
        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(self.bridge.on_message(message)))
        return at

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


class FakeHub:
    """A HiveMind hub, as far as the bridge can tell.

    ``emit`` records each message (``emitted``: ``(time, Message)``) and can
    take ``emit_delay`` seconds, like a slow socket. ``answer(message)``
    returns the reply text for an utterance, delivered as ``speak``
    ``think`` seconds after the emit, on the bus thread; ``replies`` records
    ``(time sent by the hub, channel_id)`` and ``held`` how long each bus
    handler kept the bus thread. A handler that blocks holds up every later
    hub message, exactly as on the real client.
    """

    def __init__(self, answer: Optional[Callable] = None, think: float = 0.0,
                 emit_delay: float = 0.0):
        self.answer = answer
        self.think = think
        self.emit_delay = emit_delay
        self.handlers: Dict[str, Callable] = {}
        self.emitted: List[tuple] = []
        self.replies: List[tuple] = []
        self.held: List[float] = []
        self.handshake_event = threading.Event()
        self._bus: "queue.PriorityQueue" = queue.PriorityQueue()
        self._order = 0
        self._thread = threading.Thread(target=self._run_bus, name="fake-hub-bus", daemon=True)
        self._thread.start()

    def connect(self, site_id=None, handshake_max_retries=None):
        self.handshake_event.set()

    def on_mycroft(self, msg_type, handler):
        self.handlers[msg_type] = handler

    def emit(self, hive_message):
        started = time.monotonic()
        if self.emit_delay:
            time.sleep(self.emit_delay)
        message = hive_message.payload
        self.emitted.append((started, message))
        if self.answer is not None:
            text = self.answer(message)
            if text is not None:
                self.send("speak", {"utterance": text}, dict(message.context),
                          at=time.monotonic() + self.think)

    def close(self):
        self._bus.put((float("inf"), 0, None))

    def send(self, msg_type: str, data: dict, context: dict, at: Optional[float] = None):
        """Deliver a hub message to the bridge at ``at`` (default: now)."""
        self._order += 1
        self._bus.put((time.monotonic() if at is None else at, self._order,
                       (msg_type, data, context)))

    def _run_bus(self):
        from ovos_bus_client.message import Message

        while True:
            due, _, item = self._bus.get()
            if item is None:
                return
            wait = due - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            msg_type, data, context = item
            if msg_type == "speak":
                self.replies.append((due, context.get("channel_id")))
            handler = self.handlers.get(msg_type)
            if handler is not None:
                started = time.monotonic()
                handler(Message(msg_type, data, context))
                self.held.append(time.monotonic() - started)


def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()
