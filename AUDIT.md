Last Edit: Claude Opus 5.5 - 2026-11-03 - Motive: Polish pass: sections 3, 4 and 9 fixed; findings 10-19 added (fixed in this pass, or open and low), review findings in 19.

# Audit: hivemind-discord-bridge

Evidence: `python -m pytest tests -q` (92 passed, 2026-11-03, Python 3.12 and 3.14) and reading the code at commit `9b5a0485b10d` (the line numbers below refer to it). Sections 1-9 were first written against `163a59d61a9c`.

## 1. Every channel is allowed unless an allow-list is set

- **Where:** `hivemind_discord_bridge/__init__.py:222`.
- **Finding:** with no `--allowed-channel`, any channel the bot is in reaches the hub.
- **Impact:** anyone who can message the bot spends the hub's capacity. A managed deployment should always set the allow-list.
- **Fixed 2026-11-03 (direct messages):** DMs used to bypass the allow-list. They now follow `--dm-policy` (`DISCORD_DM_POLICY`): `auto`, the default, refuses DMs when `--allowed-channel` is given (`HiveMindDiscordBridge.on_message`, `dm_policy`). Tests: `tests/test_bridge.py::test_a_dm_cannot_step_around_the_channel_list` and the four next to it.

## 2. No per-channel or per-person rate limit in the bridge

- **Where:** `HiveMindDiscordBridge.forward_to_hivemind` (`hivemind_discord_bridge/__init__.py:482`).
- **Finding:** every addressed message is forwarded immediately. The hub's admission chain can limit per person (a refusal now gets the "cannot answer right now" line, section 13); the bridge itself does not.
- **Impact:** one person can flood the hub where the hub sets no limit.

## 3. The fallback reply is English only

- **Fixed 2026-11-03.** The bridge's lines live in `hivemind_discord_bridge/lines.py` (`bridge_line`; en, fr, es, de, it, pt, nl), chosen from `HIVEMIND_LANG`, then English (`HiveMindDiscordBridge.line`); a Discord message carries no language of its own. Tests: `tests/test_lines.py`.

## 4. A reply holds the hub's bus thread for up to 10 s

- **Fixed 2026-11-03.** `speak()` schedules the send on the Discord loop and returns (`_send_in_order`, per-channel order kept); hub sends run on the `_hub_sender` thread, not on the loop. Measured in `tests/test_latency.py` (fake hub, fake channels, send 300 ms): five replies at once reached `channel.send` after a median 601.3 ms and at most 1202.2 ms before, 0.1 ms and 0.4 ms after; a reply during a 250 ms hub send waited 240.5 ms before, 0.2-0.4 ms after; the bus thread was held 300.7 ms per reply before, 0.1 ms after.

## 5. Tests do not exercise a live Discord or hub

- **Where:** `tests/conftest.py` (`FakeHub`, `FakeDiscord`, `DiscordLoop`).
- **Finding:** the fakes run the bridge on a live event loop with a real bus thread, but discord.py's gateway and HTTP client are not exercised; a real Discord API change or a real handshake is not covered.

## 6. The HiveMind client is prerelease-only

- **Where:** `requirements.txt:1` (`hivemind-bus-client>=1.0.13a1`).
- **Finding:** resolves to whatever alpha is newest at build time; images are not reproducible across builds.

## 7. Base image not pinned by digest

- **Where:** `Dockerfile:1` (`FROM python:3.14-slim`).
- **Finding:** each build can pull a different base.

## 8. A reply sent with "ping" turned off is not seen without the privileged intent

- **Where:** `hivemind_discord_bridge/__init__.py:243` (`intents.message_content = not require_mention`).
- **Finding:** in mention-only mode the bot does not request the message content intent. Discord then delivers the content of DMs and of messages that mention the bot, but a reply whose author turned off the reply ping carries no mention and arrives with empty content, so it is ignored.
- **Impact:** low; replies ping by default.

## 9. A channel the bot has not cached costs an API call per reply

- **Fixed 2026-11-03.** The bridge keeps the channel each message came from (`_keep_channel`, `_resolve_channel`, at most `CHANNELS_KEPT` = 1024), so a reply or a typing trigger calls `fetch_channel` only for a channel the bridge has not seen a message from and discord.py has not cached. Test: `tests/test_waiting.py::test_a_reply_reuses_the_channel_the_question_came_from`.

## 10. A rejected token was a crash like any other

- **Fixed 2026-11-03.** `LoginFailure`, and the gateway closing with 4004 after a token reset, ended the process with a traceback the platform could not tell from a crash worth restarting. The bridge now writes `token_rejected` to `/dev/termination-log` and exits with status 3 (`_run_discord`, `__main__.main`). Tests: `tests/test_token.py`.

## 11. The no-answer line never fired against a current hub

- **Fixed 2026-11-03.** The bridge listened only for `hive.complete_intent_failure`; HiveMind-core relays ovos-core's `complete_intent_failure`. Both are handled (`INTENT_FAILURE_TYPES`). Test: `tests/test_lines.py::test_current_and_older_hubs_no_answer_is_handled`.

## 12. A failed hub send logged the person's words, and a slow one stalled the gateway

- **Fixed 2026-11-03.** `HiveMessageBusClient.emit` raises `RuntimeError("... Failed to emit : <serialized message>")` when the hub is down, and it ran on the Discord loop, so a slow hub socket also held up heartbeats. It now runs on `_hub_sender`, a failure is logged by kind only and the person is told at once (`on_message`, `_give_up`). Test: `tests/test_bridge.py::test_a_failed_hub_send_never_logs_the_text`.

## 13. A refusal by the hub names no channel

- **Where:** `HiveMindDiscordBridge.handle_denied`, `_denied` (`hivemind_discord_bridge/__init__.py:543`, `:715`).
- **Finding:** `hive.policy.denied` carries no context from the refused message, so the bridge tells the person at once only when exactly one question is waiting; otherwise they hear "cannot answer right now" at the reply timeout (60 s). Status: open, low; needs the hub to echo the refused message's context (SUGGESTIONS.md).

## 14. Long replies were lost; replies could ping everyone

- **Fixed 2026-11-03.** A reply over 2000 characters failed whole; it is now split (`hivemind_discord_bridge/split.py`). A reply echoing "@everyone", a role or a user pinged them; replies are sent with `AllowedMentions.none()` (`NO_PINGS`). Tests: `tests/test_split.py`.

## 15. A typing trigger can land after the reply

- **Where:** `_keep_typing`, `_send_typing` (`hivemind_discord_bridge/__init__.py:738`, `:747`).
- **Finding:** typing is its own request and replies never wait for it; if Discord applies an in-flight trigger after the message, "typing…" shows for a few seconds after the answer. Status: open, low, by choice (waiting would delay every fast reply by a round trip).

## 16. Other fatal gateway closes are plain crashes

- **Where:** `HiveMindDiscordBridge._run_discord`.
- **Finding:** 4014 (privileged intent not enabled in the Developer Portal, with `--no-require-mention`) and 4013 (invalid intents) end the process with a traceback and a restart, which cannot help. Status: open, low (SUGGESTIONS.md).

## 17. The default session scope is the channel

- **Where:** `hivemind_discord_bridge/__init__.py:167` (`session_scope: str = "channel"`).
- **Finding:** the bridge contract names `person` as the default; this bridge defaults to `channel` and the platform sets `DISCORD_SESSION_SCOPE`. Status: open, by design until the platform changes its default.

## 18. Every start warned about hub permissions

- **Fixed 2026-11-03.** `start()` logged a WARNING that a fresh client is denied every message type until an admin runs `hivemind-core allow-msg ...`. On a managed hub the platform grants `recognizer_loop:utterance`, so the line was false and sent readers of the log the wrong way. The hint is now logged once, as a WARNING, only when the hub refuses an utterance with `hive.policy.denied` code `acl_disallowed_type` (`HiveMindDiscordBridge.handle_denied`, `ACL_DISALLOWED_TYPE`). Tests: `tests/test_acl_hint.py`.

## 19. Findings carried over from the Telegram bridge's review

- **Fixed 2026-11-03**, from the automated review of thalovant/hivemind-telegram-bridge#3, where this bridge has the same code:
  - A late answer to a question already given up on ended every other wait in the channel; it now ends nothing, however late (a reply that names a session only ends a question of that session). Tests: `test_a_late_answer_to_an_expired_question_leaves_other_waits_alone`, `test_an_answer_for_a_session_with_nothing_waiting_ends_no_wait`.
  - `stop()` closed the Discord client under replies being sent; `_drain_and_close` gives them `SEND_DRAIN` (5 s) first. Test: `test_stopping_lets_a_reply_being_sent_finish`. Only `stop()` drains: a SIGTERM still ends the process at once (SUGGESTIONS.md).
  - A rejected token left the health port bound; it is released (`start`). Test: `tests/test_token.py::test_a_rejected_token_frees_the_health_port`.
  - Negative or NaN `max_message_age` and negative or non-finite `reply_timeout` are refused; click's `FloatRange` let `inf` and `nan` through (`__init__`, `__main__._finite`). Tests: `test_nonsense_timings_are_refused`, `tests/test_cli.py::test_the_reply_timeout_defaults_to_a_minute_and_must_be_a_number`.
- **Fixed 2026-11-03**, from the automated review of thalovant/hivemind-discord-bridge#5 (carried over to the Telegram bridge as well):
  - A slow hub socket queued questions for the hub-send thread without limit, and a queued one could be sent after the person had been told the bridge cannot answer. At most `HUB_BACKLOG_MAX` (64) wait; beyond that the person is told at once; a question that waited past the reply timeout is not sent (`_forward_unless_late`). Tests: `test_a_hub_that_does_not_keep_up_gets_no_endless_queue`, `test_a_question_that_waited_past_the_timeout_for_the_hub_is_not_sent`.
  - A refusal was pinned on the only waiting question, even one asked long before (whose refusal had come and gone). The hub refuses at admission, so a refusal now goes to a question only when that is the one question asked in the last `REFUSAL_WINDOW` (5 s) (`_denied`, `_recent`); otherwise the reply timeout tells the person. Tests: `test_a_refusal_is_not_pinned_on_one_of_several_waiting_questions`, `test_a_refusal_long_after_the_only_question_is_not_pinned_on_it`.
  - A stale message was dropped at DEBUG, so a wrong host clock silently dropped everything; drops are logged at INFO with the age (`_is_stale`). Test: `tests/test_stale.py::test_a_dropped_message_is_logged_with_its_age_not_its_text`.
- **Fixed 2026-11-03 (owner's call on two review threads of #5 first answered "kept on purpose"):**
  - Waits were per session: in a channel-scope channel a second question restarted the first one's wait, the first answer ended both, and a per-person throttle kept a second line from coming. Every question now has its own wait (`_Question` in `_pending`, oldest first per session), its own timeout and its own "cannot answer right now" line; "typing…" shows while any question in the channel waits. A reply (`speak`, or the no-answer line) ends only the oldest waiting question of its session; the `ovos.utterance.handled` that follows it ends nothing more (`_replied` pairs them), and alone it ends one question quietly. A further sentence or a late answer for a session with nothing waiting ends no one else's wait. Tests: `tests/test_waiting.py::test_two_people_in_one_channel_each_keep_their_wait`, `test_one_person_asking_twice_gets_one_wait_per_question`, `test_a_second_sentence_of_an_answer_ends_no_other_wait`, `test_each_unanswered_question_gets_its_own_line`, `test_each_question_in_a_shared_channel_is_told_when_the_hub_is_down`.
  - An answer with no session ended every wait in the channel; it now ends only the oldest waiting question in the channel, that person's when the hub names them (`_question_for`). Test: `test_an_answer_with_no_session_ends_only_the_oldest_wait`.
  - A second sentence of Alice's answer ended Bob's wait in a channel-scope channel (one session). A reply is matched by the session and the person the hub echoes back (`context.user.discord_user_id`): a further sentence or a late answer for a person with nothing waiting ends no one's wait. Test: `test_in_a_shared_channel_a_second_sentence_ends_no_other_wait`.
  - A question could reach the hub once older than `max_message_age` if it waited less than the reply timeout in the hub queue; it is now dropped then too (`_forward_unless_late`). Test: `test_a_question_that_got_too_old_in_the_hub_queue_is_not_sent`.
  - Replies queued for one channel were unbounded; at most `SENDS_PER_CHANNEL_MAX` (32) wait, more are dropped and logged (`_send_in_order`). Test: `test_replies_queued_for_one_channel_are_capped`.
  - A reply Discord refused left its question without a word (its wait had ended when the reply came); the person now gets the "cannot answer right now" line (`_send_reply`). Test: `tests/test_split.py::test_a_reply_discord_refuses_is_followed_by_the_cannot_answer_line`.
  - Splitting copied the rest of the reply after every piece (quadratic: 15 MB took 3.4 s on the loop); it walks the text by offset, with the same pieces (`split_message`). Test: `test_splitting_stays_linear_in_the_length`. A whitespace run longer than a whole message is shortened, since Discord refuses an empty message; the docstrings now say so (`test_an_interior_whitespace_run_longer_than_a_piece_is_shortened_not_sent_empty`).
  - Tests: the slow-hub test times the second reply too, the language test requires all seven languages, the real `HiveMessageBusClient` constructor is exercised once (from the review of #2), the health socket is checked closed.
  - CI (from the review of #2): the image job smoke-tests the pushed digest before signing it and before `:latest` moves (`.github/workflows/ci.yml`); the base image is still not pinned by digest (section 7: no automated digest updates on this fork yet, so a pin would freeze security fixes).
  - Residual, low: a reply carries no question id, so when one person has two questions waiting in a session, a two-sentence answer to the first can end the second's wait early (no false "cannot answer" line comes of it).
- **Not applicable here:** handler blocking (discord.py runs each `on_message` as its own task), per-person languages (a Discord message carries none), flood-control budget (discord.py waits out 429s itself).
