"""The CLI resolves its settings from flags, the environment and secret files.

A secret on the command line is readable by every process on the host
(``ps``, ``/proc/<pid>/cmdline``), so a container must be able to start the
bridge with no secret in its arguments at all.
"""
from pathlib import Path

import pytest
from click.testing import CliRunner

import hivemind_discord_bridge.__main__ as cli


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "connect_discord_to_hivemind", lambda **kw: calls.append(kw))
    return calls


def run(args, env, started):
    result = CliRunner().invoke(cli.main, args, env=env)
    return result, (started[-1] if started else None)


CLEAN = {name: None for name in (
    "DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN_FILE", "DISCORD_ALLOWED_CHANNELS",
    "HIVEMIND_ACCESS_KEY", "HIVEMIND_ACCESS_KEY_FILE", "HIVEMIND_PASSWORD",
    "HIVEMIND_PASSWORD_FILE", "HIVEMIND_HOST", "HIVEMIND_PORT", "HIVEMIND_SITE_ID",
    "HIVEMIND_SELF_SIGNED", "HIVEMIND_LANG", "DISCORD_REQUIRE_MENTION",
    "DISCORD_SESSION_SCOPE", "BRIDGE_HEALTH_PORT")}


def test_everything_can_come_from_the_environment(started):
    result, options = run([], {**CLEAN,
        "DISCORD_BOT_TOKEN": "tok",
        "DISCORD_ALLOWED_CHANNELS": "42, 43",
        "HIVEMIND_ACCESS_KEY": "key",
        "HIVEMIND_PASSWORD": "pw",
        "HIVEMIND_HOST": "hub.local",
        "HIVEMIND_PORT": "6000",
        "HIVEMIND_SITE_ID": "lounge",
        "HIVEMIND_SELF_SIGNED": "true",
        "HIVEMIND_LANG": "fr-fr",
        "DISCORD_SESSION_SCOPE": "person",
        "BRIDGE_HEALTH_PORT": "8080",
    }, started)
    assert result.exit_code == 0, result.output
    assert options == {
        "token": "tok", "key": "key", "password": "pw", "host": "ws://hub.local",
        "port": 6000, "self_signed": True, "lang": "fr-fr", "site_id": "lounge",
        "allowed_channels": [42, 43], "require_mention": True,
        "session_scope": "person", "health_port": 8080,
    }


def test_secrets_can_come_from_files(tmp_path, started):
    token = tmp_path / "token"
    token.write_text("tok-from-file\n", encoding="utf-8")
    password = tmp_path / "password"
    password.write_text("pw-from-file", encoding="utf-8")
    result, options = run([], {**CLEAN, "DISCORD_BOT_TOKEN_FILE": str(token),
                               "HIVEMIND_PASSWORD_FILE": str(password)}, started)
    assert result.exit_code == 0, result.output
    assert options["token"] == "tok-from-file"
    assert options["password"] == "pw-from-file"


def test_a_flag_wins_over_the_environment_and_the_environment_over_a_file(tmp_path, started):
    token = tmp_path / "token"
    token.write_text("from-file", encoding="utf-8")
    env = {**CLEAN, "DISCORD_BOT_TOKEN": "from-env", "DISCORD_BOT_TOKEN_FILE": str(token)}
    assert run(["--token", "from-flag"], env, started)[1]["token"] == "from-flag"
    assert run([], env, started)[1]["token"] == "from-env"


def test_a_missing_token_is_a_usage_error(started):
    result, options = run([], CLEAN, started)
    assert result.exit_code != 0
    assert "DISCORD_BOT_TOKEN" in result.output
    assert options is None


def test_an_unreadable_secret_file_names_the_variable(tmp_path, started):
    result, _ = run([], {**CLEAN, "DISCORD_BOT_TOKEN_FILE": str(tmp_path / "absent")}, started)
    assert result.exit_code != 0
    assert "DISCORD_BOT_TOKEN_FILE" in result.output


def test_a_malformed_channel_list_is_a_usage_error(started):
    result, _ = run(["--token", "tok"], {**CLEAN, "DISCORD_ALLOWED_CHANNELS": "42,general"}, started)
    assert result.exit_code != 0


def test_the_image_passes_no_secret_on_the_command_line():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = next(line for line in dockerfile.splitlines() if line.startswith("ENTRYPOINT"))
    for secret in ("--token", "--password", "--access-key", "$DISCORD_BOT_TOKEN",
                   "$HIVEMIND_PASSWORD"):
        assert secret not in entrypoint


def test_mention_gating_is_on_by_default_and_can_be_turned_off(started):
    assert run(["--token", "t"], CLEAN, started)[1]["require_mention"] is True
    assert run(["--token", "t", "--no-require-mention"], CLEAN, started)[1]["require_mention"] is False
    off = {**CLEAN, "DISCORD_REQUIRE_MENTION": "false"}
    assert run(["--token", "t"], off, started)[1]["require_mention"] is False


def test_the_session_scope_defaults_to_the_channel_and_rejects_anything_else(started):
    assert run(["--token", "t"], CLEAN, started)[1]["session_scope"] == "channel"
    result, _ = run(["--token", "t"], {**CLEAN, "DISCORD_SESSION_SCOPE": "room"}, started)
    assert result.exit_code != 0
