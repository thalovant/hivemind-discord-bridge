Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Direct messages follow --dm-policy (refused by default when channels are listed).

# Suggestions: hivemind-discord-bridge

## Direct messages from chosen people

- **Problem:** `--dm-policy` (done 2026-11-03) is all or nothing: `deny` or `allow`.
- **Proposal:** an `allowed-users` value with a user-id allow-list, by flag and environment variable, for an owner who wants to talk to the bot privately.
- **Impact:** small; the safe default is already in place.

## Per-person rate limit

- **Problem:** AUDIT.md section 2: one person can flood the hub.
- **Proposal:** a token bucket per (channel, person) in `HiveMindDiscordBridge.forward_to_hivemind`, with a short "slow down" reply, configurable by flag and environment.
- **Impact:** high for any shared channel; small change.

## Reply off the bus thread

- **Problem:** AUDIT.md section 4.
- **Proposal:** schedule the send and return; log failures from the future's callback instead of waiting.
- **Impact:** medium; removes head-of-line blocking.

## Localised fallback line

- **Problem:** AUDIT.md section 3.
- **Proposal:** take the line from a small per-language table keyed by `--lang`, or let the hub supply it.
- **Impact:** low.

## Reproducible images

- **Problem:** AUDIT.md sections 6 and 7.
- **Proposal:** pin the base image by digest and build from a constraints file generated in CI; Renovate updates both.
- **Impact:** medium; makes a rollback mean something.

## Platform integration

- **Proposal:** run the image as the managed bridge workload described in the Thalovant bridges plan: a platform-set connection kind, credentials and identity from a Secret, `--session-scope person`, `--health-port` for probes, allow-list always set.
- **Impact:** high; this is what the fork is for.
