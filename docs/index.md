Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: rejected token reported, Discord and hub never wait for each other, stale messages, localized lines, typing..., long replies, no pings.

# hivemind-discord-bridge

A HiveMind bridge for Discord: a satellite whose input and output are a Discord bot instead of a microphone. Messages addressed to the bot become `recognizer_loop:utterance` on the hub; `speak` replies go back to the same channel.

- How to run it, every flag and its environment variable: [README](../readme.md)
- Facts for lookup: [QUICK_FACTS.md](../QUICK_FACTS.md)
- Questions: [FAQ.md](../FAQ.md)
- Known issues: [AUDIT.md](../AUDIT.md)
- Proposed work: [SUGGESTIONS.md](../SUGGESTIONS.md)
- What changed and how it was verified: [MAINTENANCE_REPORT.md](../MAINTENANCE_REPORT.md)

## Code map

| File | What it holds |
| --- | --- |
| `hivemind_discord_bridge/__init__.py` | `HiveMindDiscordBridge`: connection lifecycle (`_run_discord`), `addressed_text` (mention gating), `_is_stale`, `session_id`, `ready`, reply path (`speak`, `_send_in_order`, `_resolve_channel`), waiting for the hub (`_expect_answer`, `_wait_for_answer`, typing); `TokenRejected`, `report_token_rejected` |
| `hivemind_discord_bridge/lines.py` | `bridge_line`: the bridge's own lines (no answer, cannot answer now) per language |
| `hivemind_discord_bridge/split.py` | `split_message`: a long reply as several messages, in order, walked by offset |
| `hivemind_discord_bridge/__main__.py` | `main`: flags, environment variables, `*_FILE` secrets; a rejected token becomes `token_rejected` and exit status 3 |
| `tests/conftest.py` | `FakeHub` (stands in for `HiveMessageBusClient`), `FakeDiscord`/`FakeChannel`, `DiscordLoop` (dispatches like discord.py) |
| `hivemind_discord_bridge/health.py` | `HealthServer`: `/healthz` and `/readyz` |
| `Dockerfile` | the image: unprivileged user, entrypoint without arguments |
| `.github/workflows/ci.yml` | tests, lint, image smoke test, signed publish to `ghcr.io/thalovant/hivemind-discord-bridge` |

Related: the HiveMind hub ([HiveMind-core](https://github.com/JarbasHiveMind/HiveMind-core)) and client library ([hivemind-bus-client](https://github.com/JarbasHiveMind/hivemind-bus-client)).
