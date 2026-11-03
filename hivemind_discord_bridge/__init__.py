"""HiveMind <-> Discord bridge.

A HiveMind bridge is a satellite whose input and output are a chat
platform instead of a microphone. This one uses
:class:`~hivemind_bus_client.HiveMessageBusClient` directly (same shape as
``hivemind-telegram-bridge``'s ``HiveMindTelegramBridge``) and
``discord.py`` v2's async ``Client`` for the Discord side.

Connection lifecycle, spelled out because getting it wrong is the
recurring bug across every HiveMind bridge written so far:

- ``HiveMessageBusClient.connect()`` already starts and owns the
  reconnect worker in a background thread, and blocks synchronously
  until the handshake completes (or fails). Call it exactly once, from
  ``start()``. Do not also call ``run_forever()`` afterwards -- there is
  nothing left to start, the connection is already live in its own
  thread for as long as the process runs.
- Nothing is forwarded to HiveMind before ``connect()`` returns, and the
  bot's own messages are filtered out before they are ever considered,
  so neither an unauthenticated connection nor a feedback loop of the
  bot talking to itself reaches the hub.
- host/port are configuration, not constants (``ws://127.0.0.1:5678``
  is only ever a default value).
- the hub decides which message types this client may send (on a
  self-managed hub an admin runs ``hivemind-core allow-msg
  recognizer_loop:utterance <client_id>``); this bridge cannot do that
  step itself. It says so in its log only when the hub actually refuses
  an utterance for that reason (``hive.policy.denied``, code
  ``acl_disallowed_type``). See the README.

Threads, and why neither side waits for the other:

- the asyncio loop runs discord.py (gateway, sends, "typing…") and owns
  every piece of waiting state;
- the HiveMind client's bus thread delivers the hub's messages one after
  the other. ``speak()`` only schedules the send on the loop and returns:
  a send that waited there would hold up every later hub message;
- one hub-send thread runs every ``emit()``: never on the loop, where a
  slow hub socket would hold up every reply and the gateway heartbeat,
  and one at a time, in order.
"""
import asyncio
import itertools
import math
import re
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from functools import partial
from typing import Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

import discord
from hivemind_bus_client import (
    HiveMessage,
    HiveMessageType,
    HiveMessageBusClient,
)
from ovos_bus_client.message import Message
from ovos_utils.log import LOG

from hivemind_discord_bridge.health import HealthServer
from hivemind_discord_bridge.lines import NO_ANSWER, UNAVAILABLE, bridge_line
from hivemind_discord_bridge.split import DISCORD_LIMIT, split_message

platform = "HiveMindDiscordBridgeV0.1"

# hivemind-bus-client >= 1.0.13a1 makes HiveMessageBusClient.connect()
# block on the handshake; handshake_max_retries=None (the client's own
# default) retries forever. Bound it here so a stalled/unreachable hub
# (down, wrong password) fails fast instead of hanging the bridge.
DEFAULT_HANDSHAKE_MAX_RETRIES = 10

#: How conversations are kept apart on the hub. ``channel``: one session per
#: Discord channel or DM, shared by everyone in a channel. ``person``: one
#: session per person in each channel, so each person's follow-up questions
#: continue their own conversation.
SESSION_SCOPES = ("channel", "person")
#: Who may talk to the bot in a direct message. ``"auto"`` refuses DMs when
#: ``allowed_channels`` restricts the bot to some channels (a channel list
#: that anyone could step around by messaging the bot directly is no limit)
#: and accepts them otherwise.
DM_POLICIES = ("auto", "allow", "deny")

#: A message older than this (seconds) when the bridge reads it is not
#: answered. Discord does not replay history on connect, but a gateway
#: RESUME after an outage delivers the events missed meanwhile, and a reply
#: to a question asked long before means nothing any more.
MAX_MESSAGE_AGE = 120

#: ovos-core's "no skill could answer" and the name older hubs relayed it under.
INTENT_FAILURE_TYPES = ("complete_intent_failure", "hive.complete_intent_failure")
#: ovos-core's "this utterance is dealt with", sent after the answer, or
#: alone when the hub chose not to answer (a cancelled utterance).
HANDLED_TYPE = "ovos.utterance.handled"
#: HiveMind-core's refusal of a message (quota, rate limit, agent down).
DENIED_TYPE = "hive.policy.denied"
#: The refusal code for a message type the hub does not allow this client.
ACL_DISALLOWED_TYPE = "acl_disallowed_type"

#: Seconds the bridge waits for the hub's answer, showing "typing…", before
#: it says it cannot answer. ``0`` turns waiting off (no typing, no line).
REPLY_TIMEOUT = 60.0
#: Discord shows "typing…" for 10 s after a typing trigger: renew it before.
TYPING_EVERY = 8.0
#: Questions waiting for the hub-send thread at most. Beyond that the hub
#: is not keeping up: the person is told at once instead of queueing more.
HUB_BACKLOG_MAX = 64
#: Replies mention no one: not @everyone, @here, a role, nor a person.
NO_PINGS = discord.AllowedMentions.none()
#: When the bridge stops, replies already being sent get this long to finish.
SEND_DRAIN = 5.0
#: How long a session that asked something is remembered, so a late answer
#: or a second sentence for it is recognised and ends no one else's wait.
SEEN_KEPT = 600.0
#: How many channels' objects are kept, so a reply or "typing…" does not
#: cost a fetch_channel API call.
CHANNELS_KEPT = 1024

#: What the bridge writes to the termination log when Discord refuses the
#: bot token, so the platform can ask for a new token instead of restarting
#: a bridge that can never log in.
TOKEN_REJECTED = "token_rejected"
#: Where a container says why it stopped (Kubernetes' default
#: ``terminationMessagePath``).
TERMINATION_LOG = "/dev/termination-log"
#: Exit status when Discord refuses the bot token.
EXIT_TOKEN_REJECTED = 3
#: The gateway's close code for a token it does not accept ("Authentication
#: failed"): how a token reset while the bridge runs shows up.
GATEWAY_AUTHENTICATION_FAILED = 4004


class TokenRejected(RuntimeError):
    """Discord refused the bot token.

    ``LoginFailure`` (401 on login) at start, or the gateway closing with
    4004 (authentication failed) later, after the token was reset.
    """


def report_token_rejected(path: Optional[str] = None) -> None:
    """Write exactly ``token_rejected`` to the termination log.

    A file that cannot be written (not in a container, read-only) is
    ignored: the exit status still says the bridge stopped.
    """
    try:
        with open(path or TERMINATION_LOG, "w", encoding="utf-8") as handle:
            handle.write(TOKEN_REJECTED)
    except OSError:
        pass


def _is_token_rejection(error: BaseException) -> bool:
    if isinstance(error, discord.LoginFailure):
        return True
    return (isinstance(error, discord.ConnectionClosed)
            and getattr(error, "code", None) == GATEWAY_AUTHENTICATION_FAILED)


class HiveMindDiscordBridge:
    """Bridge a Discord bot to a HiveMind node."""

    def __init__(self,
                 token: Optional[str] = None,
                 key: Optional[str] = None,
                 password: Optional[str] = None,
                 host: Optional[str] = None,
                 port: int = 5678,
                 self_signed: bool = False,
                 lang: str = "en-us",
                 site_id: str = "discord",
                 allowed_channels: Optional[Iterable[int]] = None,
                 handshake_max_retries: int = DEFAULT_HANDSHAKE_MAX_RETRIES,
                 require_mention: bool = True,
                 session_scope: str = "channel",
                 health_port: Optional[int] = None,
                 dm_policy: str = "auto",
                 max_message_age: float = MAX_MESSAGE_AGE,
                 reply_timeout: float = REPLY_TIMEOUT,
                 *,
                 client: Optional[HiveMessageBusClient] = None,
                 discord_client: Optional[discord.Client] = None):
        """
        Parameters
        ----------
        token: Discord bot token from the Developer Portal. Required
            unless ``discord_client`` is supplied (tests inject a
            pre-built Client).
        key, password, host, port, self_signed: HiveMind hub connection.
        lang: default utterance language tag.
        site_id: this bridge's HiveMind site id.
        allowed_channels: if given, only messages from these Discord
            text channel ids are forwarded; leave unset to accept any
            channel the bot can read. Direct messages follow ``dm_policy``.
        handshake_max_retries: bound on HiveMessageBusClient's handshake
            retries; without it the client's own default of None retries
            forever and hangs the bridge if the hub never answers.
        require_mention: in a server channel, forward only messages that
            mention the bot or reply to one of its messages; DMs are always
            forwarded. A bot that forwards every message asks the hub about
            every conversation in every channel it can read. It also needs
            Discord's privileged message content intent, which it then does
            not request: Discord delivers the content of DMs and of messages
            that mention the bot without it.
        session_scope: ``"channel"`` (one hub session per channel or DM) or
            ``"person"`` (one per person in each channel). In a DM the two
            are the same conversation.
        health_port: if set, serve ``/healthz`` and ``/readyz`` on this
            port (see :mod:`hivemind_discord_bridge.health`).
        dm_policy: ``"deny"`` ignores direct messages, ``"allow"`` forwards
            them, ``"auto"`` (default) denies them when ``allowed_channels``
            is set and allows them otherwise.
        max_message_age: messages sent longer ago than this many seconds
            are not answered (they were delivered late, after an outage).
        reply_timeout: seconds to wait for the hub's answer, showing
            "typing…" in the channel, before saying the bridge cannot answer
            right now. A late answer is still delivered. ``0`` turns this
            off.
        client: pre-built HiveMessageBusClient (tests / advanced setups).
            NOTE: HiveMessageBusClient does NOT open a connection in
            __init__ -- call start() (or connect_hivemind()) to connect.
        discord_client: pre-built discord.Client (tests).
        """
        if discord_client is None and not token:
            raise ValueError("token is required unless a discord.Client is injected")

        self.token = token
        self.lang = lang
        self.site_id = site_id
        self.allowed_channels = set(allowed_channels) if allowed_channels else None
        self.handshake_max_retries = handshake_max_retries
        self.require_mention = require_mention
        if session_scope not in SESSION_SCOPES:
            raise ValueError(f"session_scope must be one of {SESSION_SCOPES}, "
                             f"not {session_scope!r}")
        self.session_scope = session_scope
        if dm_policy not in DM_POLICIES:
            raise ValueError(f"dm_policy must be one of {DM_POLICIES}, "
                             f"not {dm_policy!r}")
        if dm_policy == "auto":
            dm_policy = "deny" if self.allowed_channels is not None else "allow"
        self.dm_policy = dm_policy
        if not max_message_age >= 0:  # also refuses NaN
            raise ValueError(f"max_message_age must be >= 0, not {max_message_age!r}")
        self.max_message_age = max_message_age
        if not (math.isfinite(reply_timeout) and reply_timeout >= 0):
            raise ValueError("reply_timeout must be a finite number of seconds >= 0, "
                             f"not {reply_timeout!r}")
        self.reply_timeout = reply_timeout

        if discord_client is not None:
            self.discord = discord_client
        else:
            intents = discord.Intents.default()
            # Only a bot that reads every message needs the privileged intent.
            intents.message_content = not require_mention
            self.discord = discord.Client(intents=intents)

        self.discord.event(self.on_message)

        self.client = client or HiveMessageBusClient(
            key=key,
            password=password,
            host=host,
            port=port,
            useragent=platform,
            self_signed=self_signed,
        )

        self.health_port = health_port
        self._health: Optional[HealthServer] = None
        self._started = False
        self._connected = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._token_rejected = False
        # Every hub send goes through this one thread: off the Discord loop
        # (a slow or reconnecting hub socket must not hold up replies or the
        # gateway heartbeat), and one at a time, in order (the client's
        # encrypted transport is not meant to be written from two threads).
        self._hub_sender = ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="hivemind-send")
        # channel id -> [lock, sends waiting]: replies to one channel go out
        # in the order the hub spoke them; different channels do not wait
        # for each other. Only touched on the Discord loop.
        self._channel_sends: Dict[int, List] = {}
        # Questions waiting for the hub: one per question, oldest first, by
        # (channel id, session id); when each session last asked something;
        # how many replies per session are not yet followed by the hub's
        # "handled"; when the last "typing…" went to each channel; the
        # channels messages came from. Only touched on the Discord loop.
        self._pending: Dict[Tuple[int, str], Deque["_Question"]] = {}
        self._seen: Dict[Tuple[int, Optional[str]], float] = {}
        self._replied: Dict[Tuple[int, Optional[str]], int] = {}
        self._typing_at: Dict[int, float] = {}
        self._numbers = itertools.count()
        self._hub_backlog = 0  # questions submitted to the hub-send thread, not yet sent
        self._sending: Set[asyncio.Task] = set()
        self._channels: "OrderedDict[int, object]" = OrderedDict()
        self._background: Set[asyncio.Task] = set()
        self._told_about_acl = False  # bus thread only

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def connect_hivemind(self) -> None:
        """Connect to the HiveMind hub and wait for the handshake.

        Calls ``HiveMessageBusClient.connect()`` exactly once; that call
        already starts and owns the reconnect worker thread. Never call
        ``run_forever()`` in addition to this.
        """
        self.client.connect(site_id=self.site_id,
                           handshake_max_retries=self.handshake_max_retries)
        self.client.on_mycroft("speak", self.handle_speak)
        for msg_type in INTENT_FAILURE_TYPES:
            self.client.on_mycroft(msg_type, self.handle_intent_failure)
        self.client.on_mycroft(HANDLED_TYPE, self.handle_handled)
        self.client.on_mycroft(DENIED_TYPE, self.handle_denied)
        self._connected.set()
        LOG.info("== connected to HiveMind")

    def start(self) -> None:
        """Connect to HiveMind, then run the Discord bot until stopped.

        Blocks the calling thread for as long as the bot is connected to
        Discord. Intended to be the last call in a ``__main__``.

        Raises :class:`TokenRejected` when Discord refuses the bot token, at
        login or later; the HiveMind connection is closed first.
        """
        if self._started:
            return
        self._started = True
        if self.health_port is not None:
            self._health = HealthServer(self.health_port, self.ready)
            self._health.start()
        self.connect_hivemind()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_discord())
        finally:
            self._loop.close()
        if self._token_rejected:
            try:
                self.client.close()
            except Exception:
                LOG.exception("error closing HiveMind client")
            if self._health is not None:
                self._health.stop()
                self._health = None
            self._started = False
            self._connected.clear()
            raise TokenRejected("Discord rejected the bot token")

    async def _run_discord(self) -> None:
        try:
            await self.discord.start(self.token)
        except Exception as error:
            if not _is_token_rejection(error):
                raise
            LOG.error("Discord refused the bot token: it is wrong or was reset")
            self._token_rejected = True
        finally:
            if not self.discord.is_closed():
                await self.discord.close()

    def stop(self) -> None:
        """Stop the Discord client and close the HiveMind connection.

        Replies already being sent get ``SEND_DRAIN`` seconds to finish
        before the Discord client closes.
        """
        if self._loop is not None and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._drain_and_close(), self._loop)
            except RuntimeError:  # the loop closed in between
                pass
        try:
            self.client.close()
        except Exception:
            LOG.exception("error closing HiveMind client")
        self._started = False
        self._connected.clear()
        if self._health is not None:
            self._health.stop()
            self._health = None

    async def _drain_and_close(self) -> None:
        pending = [task for task in self._sending if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=SEND_DRAIN)
        await self.discord.close()

    def ready(self) -> bool:
        """Whether a message can go end to end right now.

        The HiveMind session must be live, not just once established: the
        client clears its handshake event when the connection drops and sets
        it again on reconnect. And the Discord gateway connection must be
        ready and not closed.
        """
        handshake = getattr(self.client, "handshake_event", None)
        hub_up = self._connected.is_set() and (handshake is None or handshake.is_set())
        discord_up = bool(self.discord.is_ready()) and not bool(self.discord.is_closed())
        return bool(hub_up and discord_up)

    # ------------------------------------------------------------------
    # Discord -> HiveMind
    # ------------------------------------------------------------------
    async def on_message(self, message: "discord.Message") -> None:
        """Forward an inbound Discord text message onto the HiveMind bus.

        Drops the message instead of forwarding when: it has no content,
        it came from the bot itself (would otherwise create a feedback
        loop), it is outside ``allowed_channels`` (when configured), it is a
        direct message and ``dm_policy`` refuses those, or HiveMind has not
        completed its handshake yet
        (forwarding before that point gets the connection killed by the
        hub).
        """
        if message is None or not message.content:
            return
        if self._is_stale(message):
            return

        bot_user = getattr(self.discord, "user", None)
        if bot_user is not None and message.author.id == bot_user.id:
            return

        channel = message.channel
        is_dm = isinstance(channel, discord.DMChannel) or getattr(channel, "type", None) == discord.ChannelType.private
        if is_dm and self.dm_policy == "deny":
            # Silently: answering every stranger's DM would let anyone make
            # the bot post, and say that it exists.
            LOG.debug("ignoring a direct message (dm policy: deny)")
            return
        if not is_dm and self.allowed_channels is not None and channel.id not in self.allowed_channels:
            LOG.debug(f"ignoring message from non-allowed channel {channel.id}")
            return

        text = self.addressed_text(message, is_dm)
        if text is None:
            return

        if not self._connected.is_set():
            LOG.warning("dropping Discord message, not connected to "
                       "HiveMind yet")
            return

        self._keep_channel(channel)
        session_id = self.session_id(channel.id, message.author)
        author_id = getattr(message.author, "id", None)
        if self._hub_backlog >= HUB_BACKLOG_MAX:
            LOG.warning(f"{self._hub_backlog} questions are waiting to be sent to "
                        f"the hub; not queueing one more from channel {channel.id}")
            self._spawn(self._say_unavailable(channel.id))
            return
        # Sent on the hub-send thread, never on this loop, so the loop keeps
        # sending replies (and heartbeats) while the hub socket is slow;
        # "typing…" starts while it goes.
        self._hub_backlog += 1
        sent = asyncio.get_running_loop().run_in_executor(
            self._hub_sender, self._forward_unless_late, time.monotonic(),
            text, message.author, channel.id)
        question = self._expect_answer(channel.id, session_id, author_id)
        try:
            await sent
        except Exception as error:
            # emit() puts the whole message, text included, in some of its
            # errors: name the kind of failure only.
            LOG.warning(f"could not send a message from channel {channel.id} to "
                        f"the hub: {type(error).__name__}")
            self._give_up(question, channel.id)
        finally:
            self._hub_backlog -= 1

    def _forward_unless_late(self, read_at: float, text: str, author, channel_id: int) -> bool:
        """``forward_to_hivemind``, unless the question waited too long to be sent.

        When the hub socket is slow, questions queue for the hub-send thread.
        One that waited past the reply timeout has already been told the
        bridge cannot answer (or, with waiting off, is older than
        ``max_message_age``): sending it now would bring an answer after
        that, so it is dropped.
        """
        limit = self.reply_timeout if self.reply_timeout > 0 else self.max_message_age
        waited = time.monotonic() - read_at
        if waited >= limit:
            LOG.info(f"not sending a question from channel {channel_id} that waited "
                     f"{waited:.0f} s for the hub")
            return False
        self.forward_to_hivemind(text, author, channel_id)
        return True

    def _is_stale(self, message: "discord.Message") -> bool:
        """Whether ``message`` was sent more than ``max_message_age`` ago."""
        sent = getattr(message, "created_at", None)
        if not isinstance(sent, datetime):
            return False
        age = time.time() - sent.timestamp()
        if age <= self.max_message_age:
            return False
        # INFO, not DEBUG: if every new message is dropped, the host clock is
        # probably wrong, and this line is how an operator finds out.
        LOG.info(f"not answering a message sent {age:.0f} s ago (older than "
                 f"{self.max_message_age:g} s; the host clock must be right)")
        return True

    def addressed_text(self, message: "discord.Message", is_dm: bool) -> Optional[str]:
        """The text to forward, or ``None`` when the message is not for the bot.

        The bot's mention (``<@id>`` or ``<@!id>``) is removed wherever it
        appears: "@bot what time is it" asks "what time is it". In a server
        channel, with ``require_mention``, a message that neither mentions
        the bot nor replies to it is not addressed to the bot at all.
        """
        bot_user = getattr(self.discord, "user", None)
        bot_id = getattr(bot_user, "id", None)
        text = message.content
        mentioned = False
        if bot_id is not None:
            text, count = re.subn(rf"<@!?{bot_id}>", " ", text)
            mentioned = count > 0 or any(
                getattr(user, "id", None) == bot_id for user in (getattr(message, "mentions", None) or ()))
            text = " ".join(text.split())
        if not text:
            return None
        if self.require_mention and not is_dm:
            if not (mentioned or self._replies_to_bot(message, bot_id)):
                return None
        return text

    @staticmethod
    def _replies_to_bot(message: "discord.Message", bot_id) -> bool:
        reference = getattr(message, "reference", None)
        replied = getattr(reference, "resolved", None) if reference is not None else None
        author = getattr(replied, "author", None)
        return bot_id is not None and author is not None and getattr(author, "id", None) == bot_id

    def forward_to_hivemind(self, text: str, author, channel_id: int) -> None:
        """Emit ``text`` to the hub as an utterance from ``author`` in ``channel_id``.

        Blocking (it writes to the hub socket): ``on_message`` runs it on the
        hub-send thread.
        """
        username = getattr(author, "name", None) or str(getattr(author, "id", "unknown"))
        msg = Message(
            "recognizer_loop:utterance",
            {"utterances": [text], "lang": self.lang},
            {
                "source": platform,
                "destination": "HiveMind",
                "platform": platform,
                "channel_id": channel_id,
                "user": {"discord_username": username,
                        "discord_user_id": getattr(author, "id", None)},
                "session": {"session_id": self.session_id(channel_id, author)},
            },
        )
        self.client.emit(HiveMessage(HiveMessageType.BUS, msg))

    def session_id(self, channel_id: int, author) -> str:
        """The hub session a message from ``author`` in ``channel_id`` belongs to."""
        author_id = getattr(author, "id", None)
        if self.session_scope == "person" and author_id is not None:
            return f"discord-{channel_id}-{author_id}"
        return f"discord-{channel_id}"

    # ------------------------------------------------------------------
    # HiveMind -> Discord
    # ------------------------------------------------------------------
    def handle_speak(self, message: Message) -> None:
        channel_id = message.context.get("channel_id")
        if channel_id is None:
            return
        utterance = message.data.get("utterance")
        if not utterance:
            return
        self.speak(utterance, channel_id, _session_of(message))

    def handle_intent_failure(self, message: Message) -> None:
        """No skill could answer: say so, in ``HIVEMIND_LANG``.

        The one bridge-authored reply to a hub message: the hub sent no
        utterance to relay. Current hubs relay ovos-core's
        ``complete_intent_failure``; older ones sent
        ``hive.complete_intent_failure``.
        """
        channel_id = message.context.get("channel_id")
        if channel_id is None:
            return
        LOG.info("the hub found no answer (complete intent failure)")
        self.speak(self.line(NO_ANSWER), channel_id, _session_of(message))

    def handle_handled(self, message: Message) -> None:
        """The hub is done with a question: stop waiting for it, say nothing."""
        channel_id = message.context.get("channel_id")
        if channel_id is not None:
            self._on_loop(self._handled, channel_id, _session_of(message))

    def handle_denied(self, message: Message) -> None:
        """The hub refused a question (quota, rate limit, agent unreachable).

        The refusal names no channel. When exactly one question is waiting
        it can only be that one, and the person is told now; otherwise the
        reply timeout tells them.
        """
        if message.data.get("denied_type") != "recognizer_loop:utterance":
            return
        code = message.data.get("code")
        if code == ACL_DISALLOWED_TYPE and not self._told_about_acl:
            # Said once, and only now that the hub has refused one: a managed
            # hub grants the type itself, and a hint at every start misleads.
            self._told_about_acl = True
            LOG.warning("the hub does not allow this bridge to send "
                        "recognizer_loop:utterance (acl_disallowed_type). On a "
                        "self-managed hub an admin grants it with `hivemind-core "
                        "allow-msg recognizer_loop:utterance <client_id>`.")
        else:
            LOG.info(f"the hub refused a question: {code}")
        self._on_loop(self._denied)

    def _on_loop(self, callback: Callable, *args) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:  # closed in between: shutting down
            pass

    def line(self, kind: str) -> str:
        """A line the bridge says itself (see :mod:`hivemind_discord_bridge.lines`).

        In ``lang`` (``HIVEMIND_LANG``) when there is a line in it, else in
        English: a Discord message carries no language of its own.
        """
        return bridge_line(kind, self.lang)

    def speak(self, text: str, channel_id: int, session_id: Optional[str] = None) -> None:
        """Post ``text`` back to ``channel_id``, without waiting for Discord.

        Called from the HiveMind bus's own thread, never from the
        asyncio loop discord.py runs on. Scheduling the send with
        ``run_coroutine_threadsafe`` is what makes that safe -- an
        ``await`` called directly from a foreign thread would raise or
        silently do nothing depending on timing. It returns at once: the
        bus thread also delivers every other hub message (and the client's
        keepalive), so it never waits for a send; a failure is logged when
        the send ends.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            LOG.warning("Discord loop not running yet, dropping reply")
            return
        # The length, never the text: replies carry what people asked about,
        # and a debug log is kept, shipped and read far more widely than the chat.
        LOG.debug(f"Sending message to Discord channel {channel_id}: {len(text)} characters")
        # The bookkeeping goes first, as a plain callback: callbacks run in the
        # order the hub's messages came, and an "ovos.utterance.handled"
        # right behind this reply must find it already counted (a task's
        # first step would only run after that).
        self._on_loop(self._reply_came, channel_id, session_id)
        try:
            future = asyncio.run_coroutine_threadsafe(self._send_in_order(channel_id, text), loop)
        except RuntimeError:  # the loop closed in between: shutting down
            LOG.warning("Discord loop stopped, dropping reply")
            return
        future.add_done_callback(partial(self._log_send_failure, channel_id))

    async def _send_in_order(self, channel_id: int, text: str) -> None:
        """Send ``text`` to ``channel_id`` after the replies scheduled before it."""
        entry = self._channel_sends.get(channel_id)
        if entry is None:
            entry = self._channel_sends[channel_id] = [asyncio.Lock(), 0]
        entry[1] += 1
        task = asyncio.current_task()
        if task is not None:
            self._sending.add(task)
        try:
            async with entry[0]:
                await self._send(channel_id, text)
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._channel_sends.pop(channel_id, None)
            if task is not None:
                self._sending.discard(task)
        # Discord clears "typing…" when the bot's message arrives: renew it
        # for anyone in this channel still waiting for an answer.
        self._typing_at.pop(channel_id, None)
        for question in self._waiting_in(channel_id):
            question.wake.set()

    async def _send(self, channel_id: int, text: str) -> None:
        """Post ``text``; several messages when it is too long for one.

        Mentions in the text never ping anyone: the hub's answer can echo
        what a person wrote ("say @everyone"), and the bot would otherwise
        ping the whole server for them.
        """
        channel = await self._resolve_channel(channel_id)
        for piece in split_message(text, DISCORD_LIMIT):
            await channel.send(piece, allowed_mentions=NO_PINGS)

    def _keep_channel(self, channel) -> None:
        """Remember the channel a message came from, for its replies."""
        self._channels[channel.id] = channel
        self._channels.move_to_end(channel.id)
        while len(self._channels) > CHANNELS_KEPT:
            self._channels.popitem(last=False)

    async def _resolve_channel(self, channel_id: int):
        """The channel object: remembered, cached by discord.py, or fetched (an API call)."""
        channel = self._channels.get(channel_id) or self.discord.get_channel(channel_id)
        if channel is None:
            channel = await self.discord.fetch_channel(channel_id)
            self._keep_channel(channel)
        return channel

    # ------------------------------------------------------------------
    # waiting for the hub: "typing…", then a line if no answer comes
    # (all on the Discord loop)
    # ------------------------------------------------------------------
    def _expect_answer(self, channel_id: int, session_id: str,
                       author_id: Optional[int] = None) -> Optional["_Question"]:
        """Wait for the hub's answer to one question, showing "typing…".

        Every question has its own wait, queued behind the earlier ones of
        its session. "typing…" shows in ``channel_id`` while any question
        there waits. A question with no answer within ``reply_timeout`` gets
        its own "cannot answer right now" line. Returns the question, or
        ``None`` with waiting off.
        """
        if not self.reply_timeout or self.reply_timeout <= 0:
            return None
        loop = asyncio.get_running_loop()
        key = (channel_id, session_id)
        question = _Question(channel_id, session_id, author_id,
                             loop.time() + self.reply_timeout, next(self._numbers))
        self._pending.setdefault(key, deque()).append(question)
        self._remember_session(key)
        question.task = loop.create_task(self._wait_for_answer(question))
        return question

    async def _wait_for_answer(self, question: "_Question") -> None:
        loop = asyncio.get_running_loop()
        try:
            while not question.done:
                remaining = question.deadline - loop.time()
                if remaining <= 0:
                    break
                question.wake.clear()
                self._keep_typing(question.channel_id)
                try:
                    await asyncio.wait_for(question.wake.wait(), min(TYPING_EVERY, remaining))
                except asyncio.TimeoutError:
                    pass
            if not question.done:
                LOG.info(f"no answer from the hub for a question in channel "
                         f"{question.channel_id} in {self.reply_timeout:g} s")
                self._end(question)
                await self._say_unavailable(question.channel_id)
        finally:
            self._end(question)

    def _end(self, question: "_Question") -> None:
        """Stop waiting for ``question`` (answered, given up or timed out)."""
        if question.done:
            return
        question.done = True
        question.wake.set()
        key = (question.channel_id, question.session_id)
        queue = self._pending.get(key)
        if queue is not None:
            try:
                queue.remove(question)
            except ValueError:
                pass
            if not queue:
                del self._pending[key]
        if not self._waiting_in(question.channel_id):
            self._typing_at.pop(question.channel_id, None)

    def _waiting_in(self, channel_id: int) -> List["_Question"]:
        """The questions waiting in ``channel_id``, oldest first."""
        waiting = [q for (channel, _), queue in self._pending.items() if channel == channel_id
                   for q in queue]
        return sorted(waiting, key=lambda q: q.number)

    def _remember_session(self, key: Tuple[int, Optional[str]]) -> None:
        now = time.monotonic()
        for old in [k for k, at in self._seen.items() if now - at >= SEEN_KEPT]:
            del self._seen[old]
            self._replied.pop(old, None)
        self._seen[key] = now

    def _oldest_answered_by(self, channel_id: int,
                            session_id: Optional[str]) -> Optional["_Question"]:
        """The question a hub message for ``session_id`` in ``channel_id`` is about.

        The oldest waiting question of that session. For a session with
        nothing waiting that asked recently, none: the message is another
        sentence of an answer, or a late answer, and ends no one else's
        wait. For no session (or one the bridge never saw), the oldest
        question waiting in the channel, so other people's waits go on.
        """
        queue = self._pending.get((channel_id, session_id))
        if queue:
            return queue[0]
        if session_id is not None and (channel_id, session_id) in self._seen:
            return None
        waiting = self._waiting_in(channel_id)
        return waiting[0] if waiting else None

    def _reply_came(self, channel_id: int, session_id: Optional[str]) -> None:
        """A reply (``speak``, or the no-answer line) for ``session_id``: ends one wait."""
        question = self._oldest_answered_by(channel_id, session_id)
        if question is not None:
            self._end(question)
            key = (channel_id, session_id)
            self._replied[key] = self._replied.get(key, 0) + 1
            self._remember_session(key)

    def _handled(self, channel_id: int, session_id: Optional[str]) -> None:
        """The hub's ``ovos.utterance.handled`` for ``session_id``.

        After a reply it only closes that reply's question (already ended).
        Without one, a question was dealt with silently (a cancelled
        utterance): its wait ends, quietly.
        """
        key = (channel_id, session_id)
        if self._replied.get(key):
            self._replied[key] -= 1
            return
        question = self._oldest_answered_by(channel_id, session_id)
        if question is not None:
            self._end(question)

    def _denied(self) -> None:
        # The refusal names no channel: only when exactly one question waits
        # can it be that one.
        waiting = [q for queue in self._pending.values() for q in queue]
        if len(waiting) == 1:
            self._give_up(waiting[0], waiting[0].channel_id)

    def _give_up(self, question: Optional["_Question"], channel_id: int) -> None:
        """The hub will not answer this question: stop waiting and say so now."""
        if question is not None:
            if question.done:
                return
            self._end(question)
        self._spawn(self._say_unavailable(channel_id))

    async def _say_unavailable(self, channel_id: int) -> None:
        """Say "cannot answer right now" in ``channel_id``: once per unanswered question."""
        try:
            await self._send_in_order(channel_id, self.line(UNAVAILABLE))
        except Exception as error:
            self._log_failure(channel_id, error)

    def _keep_typing(self, channel_id: int) -> None:
        """Trigger "typing…" in ``channel_id`` unless it was moments ago."""
        loop = asyncio.get_running_loop()
        last = self._typing_at.get(channel_id)
        if last is not None and loop.time() - last < TYPING_EVERY * 0.75:
            return
        self._typing_at[channel_id] = loop.time()
        self._spawn(self._send_typing(channel_id))

    async def _send_typing(self, channel_id: int) -> None:
        try:
            channel = await self._resolve_channel(channel_id)
            await channel.typing()  # awaited, not entered: one trigger, ~10 s
        except Exception as error:  # cosmetic: never let it fail anything
            LOG.debug(f"no typing indicator in channel {channel_id}: {type(error).__name__}")

    def _spawn(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    @classmethod
    def _log_send_failure(cls, channel_id: int, future: Future) -> None:
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            cls._log_failure(channel_id, error)

    @staticmethod
    def _log_failure(channel_id: int, error: BaseException) -> None:
        # Discord's own status and code ("403 Forbidden (error code: 50013):
        # Missing Permissions") help and hold no message text; anything
        # else, the kind only.
        if isinstance(error, discord.HTTPException):
            detail = f": {error.status} (error code: {error.code})"
        else:
            detail = ""
        LOG.warning(f"failed to send a reply to Discord channel {channel_id}: "
                    f"{type(error).__name__}{detail}")


class _Question:
    """One question waiting for the hub's answer."""

    __slots__ = ("channel_id", "session_id", "author_id", "deadline", "number",
                 "done", "wake", "task")

    def __init__(self, channel_id: int, session_id: str, author_id: Optional[int],
                 deadline: float, number: int):
        self.channel_id = channel_id
        self.session_id = session_id
        self.author_id = author_id  # who asked it
        self.deadline = deadline
        self.number = number  # order of asking, across the bridge
        self.done = False
        self.wake = asyncio.Event()
        self.task: Optional[asyncio.Task] = None


def _session_of(message: Message) -> Optional[str]:
    """The session id a hub message answers, when it says."""
    session = message.context.get("session")
    return session.get("session_id") if isinstance(session, dict) else None


__all__ = ["HiveMindDiscordBridge", "TokenRejected", "report_token_rejected", "platform"]
