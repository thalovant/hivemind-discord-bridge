Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: rejected token reported, Discord and hub never wait for each other, stale messages, localized lines, typing..., long replies, no pings.

# FAQ: hivemind-discord-bridge

## Why does the bot ignore some messages in a channel?

In a channel with several people, `HiveMindDiscordBridge.addressed_text` forwards only messages that mention the bot, and removes the mention first. A reply to one of the bot's messages counts when it pings the bot, which is Discord's default for replies: in the default mode the bot does not request the Message Content intent, so a reply with the ping turned off arrives without its text and is ignored. Direct messages need no mention. `--no-require-mention` (`DISCORD_REQUIRE_MENTION=false`) forwards every message, and needs the intent.

## How do I keep secrets off the command line?

Use the environment: `DISCORD_BOT_TOKEN`, `HIVEMIND_ACCESS_KEY`, `HIVEMIND_PASSWORD`, or the `_FILE` variants pointing at mounted secret files (`hivemind_discord_bridge/__main__.py`). The image's entrypoint passes no arguments.

## Why must `/home/bridge/.config/hivemind` persist?

It holds the HiveMind identity, whose key the hub pins. A new key on every restart is refused by the hub.

## Do people in one channel share a conversation?

By default yes: one hub session per channel (`HiveMindDiscordBridge.session_id`). `--session-scope person` gives each person their own session in each channel.

## What does `/readyz` check?

That the HiveMind session is live (the client's `handshake_event`) and that the Discord side is connected (`HiveMindDiscordBridge.ready`). `/healthz` only says the process answers.

## Why is the default branch `main` and not `feat/discord-bridge`?

This is the Thalovant fork. Upstream's release workflows were removed, so nothing here publishes to PyPI or to upstream's registry; images go to `ghcr.io/thalovant/hivemind-discord-bridge`.

## Does the bot need the Message Content intent?

No, by default. In mention-only mode (`--require-mention`, the default) the bot does not request it: Discord delivers the content of DMs and of messages that mention the bot without it (`HiveMindDiscordBridge.__init__`). Only `--no-require-mention` needs it, and above 100 servers Discord requires verification for it.

## What happens when the bot token is wrong or reset?

When Discord refuses the token, `LoginFailure` (401) at login or the gateway closing with 4004 (authentication failed) after a reset, the bridge closes the Discord client and its hub connection, writes exactly `token_rejected` to `/dev/termination-log` and exits with status 3 (`hivemind_discord_bridge.TokenRejected`, `report_token_rejected`, `HiveMindDiscordBridge._run_discord`). Other gateway close codes (4013 invalid intents, 4014 privileged intent not enabled) still end the process as a crash.

## Why did the bot not answer a message from a few minutes ago?

A message delivered more than 120 s after it was sent is not answered (`HiveMindDiscordBridge._is_stale`, `MAX_MESSAGE_AGE`). Discord does not replay history on connect, but a gateway RESUME after an outage delivers the events missed meanwhile. Each drop is logged at INFO with the message's age (never its text): if new messages are dropped too, the host clock is wrong; it must stay within 120 s of Discord's.

## What does the bot say when the hub cannot answer, and in which language?

"I don't know how to answer that." when no skill matched (`complete_intent_failure`), and "Sorry, I can't answer right now." when no answer came within `--reply-timeout` (60 s), the hub could not be reached, or it refused the question (`hive.policy.denied`, pinned on a question only when it is the one asked in the last 5 s), or Discord refused the hub's reply. Every question waits on its own, and each unanswered one gets its own line at its own timeout. A reply ends one question: the one whose number it echoes (the bridge puts `bridge_question` in each utterance's context). For a hub that drops that number, the session and the person a reply names are applied independently: the oldest waiting question from that person (when named) in that session (when named), else in the channel; with neither named, the channel's oldest. A reply for a person or session with nothing waiting ends nothing. Both are in `HIVEMIND_LANG` when `hivemind_discord_bridge/lines.py` has it (en, fr, es, de, it, pt, nl), else English: a Discord message carries no language of its own.

## Why does the channel show "typing…"?

From the moment a question arrives until the hub answers, the bridge triggers Discord's typing indicator every 8 s (`_expect_answer`, `_keep_typing`). It stops on the answer, on `ovos.utterance.handled`, or at the reply timeout. `--reply-timeout 0` turns it off together with the timeout line.

## How fast does a reply reach Discord?

As soon as the hub's `speak` arrives: `speak()` schedules the send on the Discord loop and returns, and hub sends run on their own thread (`_hub_sender`), so neither waits for the other. With a fake hub and fake channels (`tests/test_latency.py`), five replies at once reach `channel.send` within 0.4 ms of arriving; before this they queued behind each other's sends (up to 1.2 s with a 300 ms Discord).

## Can the bot be made to ping @everyone?

No. Replies are sent with `discord.AllowedMentions.none()` (`NO_PINGS`): a hub answer that echoes "@everyone", a role or a person is shown as text and pings nobody.

## What about a reply longer than a Discord message?

It is sent as several messages, in order, with every character kept except a whitespace run longer than a whole message, which Discord could not send (`hivemind_discord_bridge/split.py`, `split_message`); cuts fall on paragraph, line, sentence or word boundaries; only a piece with no such boundary in its second half (a word longer than the limit) is cut mid-word.

## The log says the hub does not allow this bridge to send `recognizer_loop:utterance`

The hub refused a question with `hive.policy.denied`, code `acl_disallowed_type`: its message-type ACL does not list `recognizer_loop:utterance` for this client. On a self-managed hub an admin runs `hivemind-core allow-msg recognizer_loop:utterance <client_id>` (and `speak`); on a managed hub the platform grants it, so the line means the grant is missing. The bridge logs it once, and only after such a refusal (`HiveMindDiscordBridge.handle_denied`); it no longer warns about it at every start.

