Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: rejected token reported, Discord and hub never wait for each other, stale messages, localized lines, typing..., long replies, no pings.

# HiveMind Discord Bridge

This bridges a Discord bot to a HiveMind node. A HiveMind bridge is a
satellite whose input and output are a chat platform instead of a
microphone: messages sent to the bot become HiveMind utterances, and the
hub's spoken replies are posted back into the same Discord channel.

## Creating a Discord bot, from scratch

If you have never created a Discord bot before, follow these steps in
order. None of them need any Discord server admin rights beyond the one
you own or a friendly server owner will give you.

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   and log in with your normal Discord account.
2. Click **New Application**, give it a name (this is the app's display
   name, not the bot's username yet), and accept the terms.
3. In the left sidebar, open **Bot**. Click **Add Bot** if it is not
   already there, and confirm.
4. Only if you will run the bridge with `--no-require-mention`: still on
   the **Bot** page, turn on **MESSAGE CONTENT INTENT** under "Privileged
   Gateway Intents" and save. A bot that answers only DMs and messages
   that mention it (the default) does not need it, because Discord
   delivers the content of those messages without the intent.
5. On the same page, click **Reset Token** (or **Copy** if a token is
   already shown) to get the bot token. It looks like a long string of
   letters, numbers, and dots. Treat it like a password: anyone who has
   it can make your bot post messages. Do not commit it to a repo or
   paste it into a public issue.
6. In the left sidebar, open **OAuth2 → URL Generator**. Under
   "Scopes", check **bot**. Under "Bot Permissions", check at least
   **Read Messages/View Channels** and **Send Messages** (add **Read
   Message History** too if you want it to see context). Copy the
   generated URL at the bottom of the page.
7. Paste that URL into your browser, pick the server you want to add
   the bot to, and confirm. You need "Manage Server" permission on that
   server (or ask whoever does to click through it for you).
8. The bot now appears offline in your server's member list. It comes
   online once you actually run this bridge with the token from step 5.

## Registering the bridge on the hub

Every HiveMind client needs credentials and, separately, permission to
send the message types it uses. On the machine running `hivemind-core`:

```bash
hivemind-core add-client
```

This prints an access key and password; pass them to the bridge as
`--access-key` / `--password` (or store them once with
`hivemind-client set-identity` and omit the flags).

A freshly added client is denied every message type by default. The
bridge needs at least:

```bash
hivemind-core allow-msg recognizer_loop:utterance <client_id>
hivemind-core allow-msg speak <client_id>
```

`<client_id>` is printed by `add-client` (and by `hivemind-core
list-clients` afterwards). Skipping this step is the single most common
reason a bridge "connects fine" but nothing ever seems to happen: the hub
silently drops every message the client sends until it is whitelisted.

## Running the bridge

```bash
pip install .
hivemind-discord-bridge \
  --token <your-bot-token> \
  --access-key <key> --password <password> \
  --host ws://127.0.0.1 --port 5678
```

By default the bridge answers direct messages and any text channel the
bot can read. Pass `--allowed-channel <channel_id>` (repeatable) to
restrict it to specific channels; direct messages are then refused too,
so nobody steps around the list by messaging the bot. `--dm-policy`
(`auto`, `allow` or `deny`) changes that: `auto`, the default, refuses
DMs when channels are listed and accepts them otherwise.

In a server channel, the bridge forwards only messages that mention the
bot (`@bot what time is it`), and removes the mention before forwarding, so
the hub is asked `what time is it`. A reply to one of the bot's messages
counts when it pings the bot (Discord's default for replies); a reply with
the ping turned off arrives without its text unless the message content
intent is enabled, and is ignored.
Direct messages need no mention. Because Discord delivers the content of
DMs and of messages that mention the bot without it, the bot does not
request the privileged message content intent. Pass `--no-require-mention`
to forward every message in every channel the bot can read; that mode
needs the intent enabled in the Developer Portal.

Each Discord channel (or DM) is one conversation on the hub, so in a
channel one person's follow-up ("and tomorrow?") continues whatever the
last person asked. Pass `--session-scope person` to give each person their
own conversation in each channel.

`--health-port <port>` serves two endpoints for container platforms:
`/healthz` answers 200 while the process runs, and `/readyz` answers 200
only while the HiveMind session is up and the Discord connection is ready
(503 otherwise). They serve no data and accept no input.

Useful flags:

- `--site-id`: this bridge's HiveMind site id. If you run more than one
  bridge (or more than one instance of this bridge) on the same host,
  give each a distinct site id — otherwise they collide over the same
  identity file and pinned peer keys.
- `--self-signed`: accept a self-signed TLS certificate on `wss://` hubs.
- `--lang`: the language tag attached to forwarded utterances (default
  `en-us`).

Run `hivemind-discord-bridge --help` for the full list.

Every flag also falls back to an environment variable, so the bridge can
run with no secret on its command line, where any other process on the
host could read it:

| Flag | Variable |
| --- | --- |
| `--token` | `DISCORD_BOT_TOKEN` |
| `--allowed-channel` | `DISCORD_ALLOWED_CHANNELS` (ids separated by commas) |
| `--dm-policy` | `DISCORD_DM_POLICY` (`auto`, `allow` or `deny`) |
| `--no-require-mention` | `DISCORD_REQUIRE_MENTION` (`true`/`false`) |
| `--session-scope` | `DISCORD_SESSION_SCOPE` (`channel`/`person`) |
| `--health-port` | `BRIDGE_HEALTH_PORT` |
| `--reply-timeout` | `BRIDGE_REPLY_TIMEOUT` (seconds, default 60, `0` off) |
| `--access-key` | `HIVEMIND_ACCESS_KEY` |
| `--password` | `HIVEMIND_PASSWORD` |
| `--host` | `HIVEMIND_HOST` |
| `--port` | `HIVEMIND_PORT` |
| `--site-id` | `HIVEMIND_SITE_ID` |
| `--self-signed` | `HIVEMIND_SELF_SIGNED` (`true`/`false`) |
| `--lang` | `HIVEMIND_LANG` |

The token, access key and password also accept `<VARIABLE>_FILE`, the
path of a file holding the value, which is how container platforms mount
secrets. A flag wins over its variable, and the variable over its file.

## Docker

```bash
docker build -t hivemind-discord-bridge .
docker run --rm \
  -e DISCORD_BOT_TOKEN=... \
  -e HIVEMIND_ACCESS_KEY=... \
  -e HIVEMIND_PASSWORD=... \
  -e HIVEMIND_HOST=ws://hivemind-core \
  hivemind-discord-bridge
```

or via `docker-compose.yml` — copy it, fill in the environment section,
and `docker compose up`.

The image runs as an unprivileged user and keeps the HiveMind identity in
`/home/bridge/.config/hivemind`. Mount a volume there so the identity
survives a restart: the hub pins the identity's key, and a new one on
every start is refused.

## What this bridge does, precisely

- Connects to Discord with `discord.py`'s async `Client` (gateway
  websocket); connects to the HiveMind hub with
  `hivemind_bus_client.HiveMessageBusClient`.
- Skips its own messages (so it can never talk to itself) and any
  message with no text content.
- Only forwards messages once the HiveMind handshake has completed —
  forwarding earlier would get the connection killed by the hub instead
  of just failing the one message.
- Forwards each remaining message as a `recognizer_loop:utterance` bus
  message, carrying the Discord channel id and author in the message
  context so the hub's `speak` reply can be routed back to the right
  channel.
- Posts `speak` replies back into the originating channel or DM, as soon
  as they arrive: the hub's bus thread never waits for Discord, and the
  Discord loop never waits for the hub. Replies to one channel keep their
  order; a reply longer than Discord's 2000 characters goes out as
  several messages, in order; a reply never pings anyone
  (`AllowedMentions.none()`), whatever `@everyone` or mention it echoes.
- Shows "typing…" from the moment a question arrives until the hub
  answers. With no answer within `--reply-timeout` (60 s), or at once when
  the hub cannot be reached or refuses the question just asked, it says
  it cannot answer right now. When no skill matches
  (`complete_intent_failure`) it says it does not know. These lines are
  in `HIVEMIND_LANG` when there is one for it, else English
  (`hivemind_discord_bridge/lines.py`).
- Does not answer a message delivered more than 120 s after it was sent
  (events a gateway RESUME replays after an outage).
- When Discord refuses the bot token (`LoginFailure` at login, or the
  gateway closing with 4004 later), writes `token_rejected` to
  `/dev/termination-log` and exits with status 3.

## Voice channels — not built, documented as a future direction

This bridge is text-only. Discord voice channels (joining a voice call,
transcribing speech, speaking replies with TTS) are a substantially
different integration — a persistent voice gateway connection, audio
decode/encode, and a wake-word or push-to-talk model — and are not
implemented here. If you want voice support, treat it as a separate
follow-on bridge built on `discord.py`'s voice client, not an extension
of this text bridge.

## Testing

```bash
pip install -e .[test]
pytest tests/
```

The test suite mocks both the `discord.Client` and the HiveMind
`HiveMessageBusClient` (`tests/conftest.py` has a fake hub with its own bus
thread and fake channels on a live event loop), so it runs without a live
token or hub. It has
not been exercised against a real Discord bot or a real HiveMind hub —
that needs an actual bot token, which this repository does not have.
