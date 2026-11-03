Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: rejected token reported, Discord and hub never wait for each other, stale messages, localized lines, typing..., long replies, no pings.

# Maintenance report: hivemind-discord-bridge

## 2026-11-03: Polish pass (solid, fast)

**Changes** (each its own commit on `polish/solid-fast-replies`, oldest first)

1. A rejected bot token (`LoginFailure` at login, or the gateway closing with 4004 after a reset) closes the Discord client and the hub connection, writes `token_rejected` to `/dev/termination-log` and exits with status 3 (`TokenRejected`, `_run_discord`, `__main__.main`).
2. Discord and the hub never wait for each other: `speak()` schedules the send and returns (per-channel order kept), hub sends run on one dedicated thread off the Discord loop (no more stalled heartbeats on a slow hub); a failed hub send no longer logs the person's words.
3. Messages delivered more than 120 s after they were sent (a gateway RESUME replay) are not answered (`_is_stale`).
4. The bridge's own lines in seven languages from `HIVEMIND_LANG` (`lines.py`, same table as the Telegram bridge); `complete_intent_failure`, which current hubs send, is handled.
5. "typing…" while the hub thinks; "cannot answer right now" at the reply timeout (60 s, `BRIDGE_REPLY_TIMEOUT`), or at once when the hub is unreachable or refuses the only waiting question; `ovos.utterance.handled` ends a wait quietly. Channels are remembered, so replies stop costing `fetch_channel` calls (AUDIT section 9).
6. Long replies split at 2000, every character kept (`split.py`); replies never ping anyone (`AllowedMentions.none()`); module docstring says which thread does what.
7. The start-up WARNING about `hivemind-core allow-msg` is gone: it was false on a managed hub. The hint is logged once, only when the hub refuses an utterance with `acl_disallowed_type`.
8. Docs: README, QUICK_FACTS, FAQ, AUDIT (sections 3, 4 and 9 fixed, 10-18 added), SUGGESTIONS, docs/index.md, AGENTS.md (stale handshake-retries text corrected).

**Rationale:** the owner asked for the live chat bridges to be perfect, solid, fast and polished. The rejected-token state lets the platform ask for a new token instead of restarting forever; the threading change removes head-of-line blocking that grew with every simultaneous person and kept hub slowness off the gateway; the rest closes gaps where a person got silence (no-answer never fired, long replies lost, hub down), a stale answer, or where the bot could be made to ping a whole server.

**Verification**

- `python -m pytest tests -q`: 95 passed on Python 3.14.8 and 3.12 (local venvs). New tests were checked to fail without their change (the old code at `ba1daaa`, or the change reverted in place): non-blocking speak, per-channel order, emit text in logs, stale messages, channel reuse, split, no pings, the permission hint.
- `ruff check --isolated --select F,E9 .`: clean.
- Latency, `tests/test_latency.py` (fake hub + fake channels, messages dispatched as discord.py does; send 300 ms; second scenario: hub send 250 ms):

  | Measure | Before (`ba1daaa`) | After |
  | --- | --- | --- |
  | speak -> channel.send, five replies at once, median | 601.3 ms | 0.1-0.2 ms |
  | speak -> channel.send, five replies at once, max | 1202.2 ms | 0.3-0.4 ms |
  | hub bus thread held per reply | 300.7 ms | 0.1 ms |
  | reply during a slow hub send | 240.5 ms | 0.2-0.4 ms |
  | message -> `recognizer_loop:utterance` emitted, median | 0.6 ms | 1.3-1.9 ms (hop to the hub-send thread) |
  | message -> typing triggered, median | none | 1.8 ms |

- Live check against Discord's API, not committed as a test: a made-up token through the real `discord.Client` gives `TokenRejected` in 0.12 s and leaves the client closed.
- Image built locally with podman: `--help` lists `--reply-timeout`, runs as uid 10001, refuses to start without a token and names `DISCORD_BOT_TOKEN`, the package imports on a read-only root filesystem.
- Not verified: a live Discord bot (none exists yet), a live hub, the gateway 4004 path against Discord itself (patched in `tests/test_token.py`), CI on GitHub for this branch (runs on the pull request).

**Transparency report**

- **AI model:** Claude Opus 5.5 (`claude-opus-5-5`), in Claude Code.
- **Actions taken:** wrote the code changes, the fakes in `tests/conftest.py` and every new test; measured latency on the old and new code; updated these documents.
- **Oversight:** directed by the repository owner through an orchestrating agent; no human code review yet; the pull request is not merged.

## 2026-11-03: direct messages follow --dm-policy

`--allowed-channel` did not apply to direct messages, so anyone who could DM the bot reached the hub whatever the channel list said (AUDIT.md section 1). `HiveMindDiscordBridge` gains `dm_policy` (`auto`, `allow`, `deny`; CLI `--dm-policy`, environment `DISCORD_DM_POLICY`). `auto`, the default, refuses DMs when channels are listed and accepts them otherwise, so an unconfigured bot behaves as before and a restricted one is restricted. A refused DM is dropped silently: answering strangers would let anyone make the bot post. Thalovant's managed bridges always list channels, so they refuse DMs with no change on the platform side.

Verification: `python -m pytest -q` 47 passed (Python 3.14); `ruff check --select F,E9 .` clean. The old test `test_dm_bypasses_allowed_channels`, which asserted the gap, is replaced by five tests of the policy and one CLI test.

### Transparency Report

AI Model: Claude Opus 5.5. Actions Taken: implemented `dm_policy` and the CLI option, replaced and added tests, updated README, QUICK_FACTS, AUDIT, SUGGESTIONS and AGENTS. Oversight: owner-directed ("fix the still open"); reviewed by the session lead before the pull request.

## 2026-11-02: Thalovant fork

**Changes** (each its own commit on `main`, oldest first)

1. Settings from the environment and `*_FILE` secret files; the image starts the bridge directly as uid 10001 with no secret in its arguments.
2. Mention gating in channels with the mention stripped before forwarding; DMs unchanged. The bot no longer requests the privileged message content intent.
3. Optional per-person hub sessions (`--session-scope person`).
4. Reply text no longer logged at debug level.
5. Optional `/healthz` and `/readyz` (`--health-port`).
6. Inherited upstream workflows (release, publish, image push to upstream's registry) removed; `.github/workflows/ci.yml` added (tests, lint, image smoke test; on `main` only, the image is pushed under `:sha-<commit>`, signed and verified by digest, and only then tagged `:latest`); docs set added.

**Rationale:** run the bridge as a managed workload: no secrets in process arguments, no message text in logs, no forwarding of conversations not addressed to the bot while mention gating is on (the default; `--no-require-mention` forwards every message the bot receives), and probes a platform can use.

**Verification**

- `python -m pytest tests -q`: 42 passed (Python 3.12, local). Every change has tests that fail with the change reverted and pass with it.
- `ruff check --isolated --select F,E9 .`: clean.
- Image built locally with podman: `--help` answers, runs as uid 10001, refuses to start without a token and names `DISCORD_BOT_TOKEN`.
- Not verified: CI on GitHub (the fork's Actions need enabling once in the Actions tab), a live Discord bot, a live hub.

**Transparency report**

- **AI model:** Claude Opus 5.5 (`claude-opus-5-5`), in Claude Code.
- **Actions taken:** wrote the five code changes and their tests, the CI workflow, the Dockerfile changes and these documents; ran the tests and the local image build.
- **Oversight:** directed by the repository owner (fork, fix, own CI and registry); no human code review yet.
