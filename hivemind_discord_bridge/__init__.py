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
- a freshly registered HiveMind client is denied every message type
  until a hub admin runs ``hivemind-core allow-msg
  recognizer_loop:utterance <client_id>`` (and usually ``speak`` too);
  this bridge cannot do that step itself. See the README.
"""
import asyncio
import re
import threading
from typing import Iterable, Optional

import discord
from hivemind_bus_client import (
    HiveMessage,
    HiveMessageType,
    HiveMessageBusClient,
)
from ovos_bus_client.message import Message
from ovos_utils.log import LOG

from hivemind_discord_bridge.health import HealthServer

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
        self.client.on_mycroft("hive.complete_intent_failure",
                               self.handle_intent_failure)
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
        LOG.warning(
            "a freshly registered HiveMind client is denied every message "
            "type until an admin runs `hivemind-core allow-msg "
            "recognizer_loop:utterance <client_id>` on the hub (and "
            "usually `speak` too). If messages seem to vanish silently, "
            "check that first."
        )
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
        """Stop the Discord client and close the HiveMind connection."""
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.discord.close(), self._loop)
        try:
            self.client.close()
        except Exception:
            LOG.exception("error closing HiveMind client")
        self._started = False
        self._connected.clear()
        if self._health is not None:
            self._health.stop()
            self._health = None

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

        self.forward_to_hivemind(text, message.author, channel.id)

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
        self.speak(utterance, channel_id)

    def handle_intent_failure(self, message: Message) -> None:
        channel_id = message.context.get("channel_id")
        if channel_id is None:
            return
        LOG.error("complete intent failure")
        self.speak("I don't know how to answer that", channel_id)

    def speak(self, text: str, channel_id: int) -> None:
        """Post ``text`` back to ``channel_id``.

        Called from the HiveMind bus's own thread, never from the
        asyncio loop discord.py runs on. Scheduling the send with
        ``run_coroutine_threadsafe`` is what makes that safe -- an
        ``await`` called directly from a foreign thread would raise or
        silently do nothing depending on timing.
        """
        if self._loop is None:
            LOG.warning("Discord loop not running yet, dropping reply")
            return
        # The length, never the text: replies carry what people asked about,
        # and a debug log is kept, shipped and read far more widely than the chat.
        LOG.debug(f"Sending message to Discord channel {channel_id}: {len(text)} characters")
        future = asyncio.run_coroutine_threadsafe(
            self._send(channel_id, text), self._loop
        )
        try:
            future.result(timeout=10)
        except Exception:
            LOG.exception(f"failed to send Discord message to channel={channel_id}")

    async def _send(self, channel_id: int, text: str) -> None:
        channel = self.discord.get_channel(channel_id)
        if channel is None:
            channel = await self.discord.fetch_channel(channel_id)
        await channel.send(text)


__all__ = ["HiveMindDiscordBridge", "TokenRejected", "report_token_rejected", "platform"]
