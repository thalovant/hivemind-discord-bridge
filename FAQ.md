Last Edit: Claude Opus 5.5 - 2026-11-02 - Motive: Address the CI and documentation review: publish from main only, sign before :latest, pinned audit evidence, qualified claims.

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
