"""A token Discord refuses stops the bridge with ``token_rejected``.

The platform reads the termination log to tell "replace the token" apart
from a crash worth restarting. ``LoginFailure`` (401 on login) is a
rejected token; so is the gateway closing with 4004 (authentication
failed), which is how a token reset while the bridge runs shows up. Any
other close code is not.
"""
from unittest.mock import MagicMock

import discord
import pytest
from click.testing import CliRunner

import hivemind_discord_bridge as bridge_module
from hivemind_discord_bridge import HiveMindDiscordBridge, TokenRejected


def closed_with(code):
    socket = MagicMock()
    socket.close_code = code
    return discord.ConnectionClosed(socket, shard_id=None, code=code)


@pytest.fixture
def discord_fails(monkeypatch):
    """Make the real discord.Client fail its login, or its gateway, as asked."""
    failure = {}

    async def login(self, token):
        if "login" in failure:
            raise failure["login"]

    async def connect(self, *, reconnect=True):
        if "connect" in failure:
            raise failure["connect"]

    monkeypatch.setattr(discord.Client, "login", login)
    monkeypatch.setattr(discord.Client, "connect", connect)
    return failure


def make():
    hub = MagicMock(name="HiveMessageBusClient")
    return HiveMindDiscordBridge(token="secret-token", client=hub), hub


def test_a_token_refused_at_login_stops_the_bridge(discord_fails):
    discord_fails["login"] = discord.LoginFailure("Improper token has been passed.")
    bridge, hub = make()
    with pytest.raises(TokenRejected) as raised:
        bridge.start()
    assert "secret-token" not in str(raised.value)
    hub.close.assert_called_once()
    assert bridge.discord.is_closed()


def test_a_token_reset_while_running_stops_the_bridge(discord_fails):
    discord_fails["connect"] = closed_with(4004)
    with pytest.raises(TokenRejected):
        make()[0].start()


def test_another_gateway_close_is_not_a_rejected_token(discord_fails):
    discord_fails["connect"] = closed_with(4013)  # invalid intents
    with pytest.raises(discord.ConnectionClosed):
        make()[0].start()


def test_main_writes_token_rejected_and_exits_non_zero(monkeypatch, tmp_path):
    import hivemind_discord_bridge.__main__ as cli

    log = tmp_path / "termination-log"
    monkeypatch.setattr(bridge_module, "TERMINATION_LOG", str(log))

    def refused(**_):
        raise TokenRejected("Discord rejected the bot token")

    monkeypatch.setattr(cli, "connect_discord_to_hivemind", refused)
    result = CliRunner().invoke(cli.main, ["--token", "x"], env={"DISCORD_BOT_TOKEN_FILE": None})
    assert result.exit_code == bridge_module.EXIT_TOKEN_REJECTED != 0
    assert log.read_text() == "token_rejected"


def test_an_unwritable_termination_log_is_ignored(tmp_path):
    bridge_module.report_token_rejected(str(tmp_path / "missing-dir" / "log"))
