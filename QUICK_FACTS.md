Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Direct messages follow --dm-policy (refused by default when channels are listed).

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
| Tests | `tests/`, 47 tests (`python -m pytest tests -q`) |
| HiveMind message types | sends `recognizer_loop:utterance`; handles `speak`, `hive.complete_intent_failure` |
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
| `--access-key` | `HIVEMIND_ACCESS_KEY` (or `_FILE`) |
| `--password` | `HIVEMIND_PASSWORD` (or `_FILE`) |
| `--host`, `--port` | `HIVEMIND_HOST`, `HIVEMIND_PORT` |
| `--site-id`, `--lang`, `--self-signed` | `HIVEMIND_SITE_ID`, `HIVEMIND_LANG`, `HIVEMIND_SELF_SIGNED` |
