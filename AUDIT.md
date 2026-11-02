Last Edit: Claude Opus 5.5 - 2026-11-02 - Motive: Thalovant fork set up: fixes on main, Thalovant CI and signed images, docs set added.

# Audit: hivemind-discord-bridge

Evidence: `python -m pytest tests -q` (42 passed, 2026-11-02, Python 3.12) and reading the code at `main`.

## 1. Every channel is allowed unless an allow-list is set

- **Where:** `hivemind_discord_bridge/__init__.py:118`.
- **Finding:** with no `--allowed-channel`, any channel the bot is in, and any DM, reaches the hub.
- **Impact:** anyone who can message the bot spends the hub's capacity. A managed deployment should always set the allow-list.

## 2. No per-channel or per-person rate limit

- **Where:** `HiveMindDiscordBridge.forward_to_hivemind` (`hivemind_discord_bridge/__init__.py:291`).
- **Finding:** every addressed message is forwarded immediately.
- **Impact:** one person can flood the hub.

## 3. The fallback reply is English only

- **Where:** `hivemind_discord_bridge/__init__.py:332`.
- **Finding:** `hive.complete_intent_failure` always answers "I don't know how to answer that", whatever `--lang` is.

## 4. A reply holds the hub's bus thread for up to 10 s

- **Where:** `hivemind_discord_bridge/__init__.py:353` (`future.result(timeout=10)`).
- **Finding:** `speak()` waits for the Discord send on the HiveMind bus thread, so a slow Discord API delays every other message from the hub.

## 5. Tests do not exercise a live Discord or hub

- **Where:** `tests/test_bridge.py` (module docstring).
- **Finding:** both sides are mocks; the suite checks wiring and behaviour, not a real handshake or a real Discord API change.

## 6. The HiveMind client is prerelease-only

- **Where:** `requirements.txt:1` (`hivemind-bus-client>=1.0.13a1`).
- **Finding:** resolves to whatever alpha is newest at build time; images are not reproducible across builds.

## 7. Base image not pinned by digest

- **Where:** `Dockerfile:1` (`FROM python:3.14-slim`).
- **Finding:** each build can pull a different base.

## 8. A reply sent with "ping" turned off is not seen without the privileged intent

- **Where:** `hivemind_discord_bridge/__init__.py:131` (`intents.message_content = not require_mention`).
- **Finding:** in mention-only mode the bot does not request the message content intent. Discord then delivers the content of DMs and of messages that mention the bot, but a reply whose author turned off the reply ping carries no mention and arrives with empty content, so it is ignored.
- **Impact:** low; replies ping by default.

## 9. A channel the bot has not cached costs an API call per reply

- **Where:** `hivemind_discord_bridge/__init__.py:360` (`fetch_channel`).
- **Finding:** `_send` falls back to `fetch_channel` when `get_channel` misses, on every reply to an uncached channel or DM.
- **Impact:** low; counts against Discord's rate limit under load.
