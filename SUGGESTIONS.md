Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: reply-off-the-bus-thread and localized lines done; hub refusal context and termination reasons proposed.

# Suggestions: hivemind-discord-bridge

## Direct messages from chosen people

- **Problem:** `--dm-policy` (done 2026-11-03) is all or nothing: `deny` or `allow`.
- **Proposal:** an `allowed-users` value with a user-id allow-list, by flag and environment variable, for an owner who wants to talk to the bot privately.
- **Impact:** small; the safe default is already in place.

## Per-person rate limit

- **Problem:** AUDIT.md section 2: where the hub sets no limit, one person can flood it.
- **Proposal:** a token bucket per (channel, person) in `HiveMindDiscordBridge.forward_to_hivemind`, answering with the "cannot answer right now" line (already rate limited per person, `UNAVAILABLE_EVERY`), configurable by flag and environment.
- **Impact:** high for any shared channel; small change.

## The hub echoes the refused message's context

- **Problem:** AUDIT.md section 13: `hive.policy.denied` names no channel, so with several questions waiting the person learns of a refusal only at the reply timeout.
- **Proposal:** HiveMind-core adds the refused message's `context` (or just its session id) to `hive.policy.denied`; the bridge then ends exactly that wait (`_give_up`).
- **Impact:** medium; immediate feedback on quota or rate refusals in busy channels.

## More termination reasons

- **Problem:** AUDIT.md section 16: only a rejected token is told apart from a crash.
- **Proposal:** the same termination-log mechanism for 4014 (`intent_required`: enable the Message Content intent or keep mention gating) and, once hivemind-bus-client raises a distinct error for it, a hub that refuses the bridge's credentials (`hub_rejected`).
- **Impact:** medium; the platform can tell the owner what to change instead of restarting.

## A per-person language

- **Problem:** the bridge's lines follow `HIVEMIND_LANG` only; a Discord message carries no language.
- **Proposal:** use the guild's `preferred_locale` when the server set one other than the default, behind a flag (most servers keep `en-US`, which would override a French hub).
- **Impact:** low.

## Reproducible images

- **Problem:** AUDIT.md sections 6 and 7.
- **Proposal:** pin the base image by digest and build from a constraints file generated in CI; Renovate updates both.
- **Impact:** medium; makes a rollback mean something.

## Platform integration

- **Proposal:** run the image as the managed bridge workload described in the Thalovant bridges plan: a platform-set connection kind, credentials and identity from a Secret, `--session-scope person`, `--health-port` for probes, allow-list always set, `terminationMessagePolicy` reading `/dev/termination-log`.
- **Impact:** high; this is what the fork is for.

## Done

- Reply off the bus thread (2026-11-03): AUDIT.md section 4.
- Localised fallback line (2026-11-03): AUDIT.md section 3.
