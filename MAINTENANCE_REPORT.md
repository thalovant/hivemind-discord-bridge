Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Direct messages follow --dm-policy (refused by default when channels are listed).

# Maintenance report: hivemind-discord-bridge

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
