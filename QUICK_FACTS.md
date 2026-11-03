Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: rejected token reported, Discord and hub never wait for each other, stale messages, localized lines, typing..., long replies, no pings.

# Quick facts: hivemind-discord-bridge

| Key | Value |
| --- | --- |
| Package (distribution) | `hivemind-discord-bridge` |
| Import name | `hivemind_discord_bridge` |
| Version | `0.0.1` (`hivemind_discord_bridge/version.py`) |
| Console entry point | `hivemind-discord-bridge` -> `hivemind_discord_bridge.__main__:main` |
| Main class | `hivemind_discord_bridge.HiveMindDiscordBridge` |
| Health server | `hivemind_discord_bridge.health.HealthServer` (`/healthz`, `/readyz`) |
| Image | `ghcr.io/thalovant/hivemind-discord-bridge` (`:sha-<commit>` and `:latest`, signed by digest with cosign keyless) |
| Image user | `bridge`, uid 10001; identity in `/home/bridge/.config/hivemind` |
| Default branch | `main` (upstream: `JarbasHiveMind/hivemind-discord-bridge`, branch `feat/discord-bridge`) |
| CI | `.github/workflows/ci.yml`: tests on Python 3.12 and 3.14, ruff `F,E9`, image smoke test, publish on `main` |
| Tests | `tests/`, 121 tests (`python -m pytest tests -q`); `tests/conftest.py` has a fake hub and fake channels |
| HiveMind message types | sends `recognizer_loop:utterance`; handles `speak`, `complete_intent_failure` (and the older `hive.complete_intent_failure`), `ovos.utterance.handled`, `hive.policy.denied` |
| Rejected token | `LoginFailure` or gateway close 4004: writes `token_rejected` to `/dev/termination-log`, exit status 3 (`TokenRejected`, `report_token_rejected`) |
| Bridge's own lines | `hivemind_discord_bridge/lines.py`: en, fr, es, de, it, pt, nl; `HIVEMIND_LANG`, then English |
| Limits | messages older than 120 s not answered (`MAX_MESSAGE_AGE`); reply timeout 60 s per question (`REPLY_TIMEOUT`, one wait per question); typing renewed every 8 s; replies split at 2000 (`split.py`); replies sent with `AllowedMentions.none()`; 1024 channels remembered; replies being sent get 5 s at stop (`SEND_DRAIN`); at most 64 questions queued for the hub (`HUB_BACKLOG_MAX`); at most 32 replies queued per channel (`SENDS_PER_CHANNEL_MAX`); a refusal is pinned only on the one question asked in the last 5 s (`REFUSAL_WINDOW`) |
| License | Apache-2.0 |

## Settings

| Flag | Environment variable |
| --- | --- |
| `--token` | `DISCORD_BOT_TOKEN` (or `DISCORD_BOT_TOKEN_FILE`) |
| `--allowed-channel` | `DISCORD_ALLOWED_CHANNELS` |
| `--dm-policy` | `DISCORD_DM_POLICY` (`auto`: deny when channels are listed) |
| `--no-require-mention` | `DISCORD_REQUIRE_MENTION` |
| `--session-scope` | `DISCORD_SESSION_SCOPE` (`channel` or `person`) |
| `--health-port` | `BRIDGE_HEALTH_PORT` |
| `--reply-timeout` | `BRIDGE_REPLY_TIMEOUT` (seconds, default 60, `0` turns waiting off) |
| `--access-key` | `HIVEMIND_ACCESS_KEY` (or `_FILE`) |
| `--password` | `HIVEMIND_PASSWORD` (or `_FILE`) |
| `--host`, `--port` | `HIVEMIND_HOST`, `HIVEMIND_PORT` |
| `--site-id`, `--lang`, `--self-signed` | `HIVEMIND_SITE_ID`, `HIVEMIND_LANG`, `HIVEMIND_SELF_SIGNED` |
