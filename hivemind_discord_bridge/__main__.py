"""CLI entry point for the HiveMind <-> Discord bridge.

HiveMind identity (key/password/host/port) defaults to the values stored
by ``hivemind-client set-identity``; flags override them.

Every option can also come from the environment, so a container never has
to put a secret on its command line, where any process on the host can
read it. Secrets additionally accept a ``*_FILE`` variable naming a file
that holds the value, the convention for secrets mounted into a container.
A flag wins over its environment variable, and the variable over its file.
"""
import os

import click
from ovos_utils.log import LOG

from hivemind_discord_bridge import HiveMindDiscordBridge


def connect_discord_to_hivemind(token, key=None, password=None,
                                host=None, port=5678, self_signed=False,
                                lang="en-us", site_id="discord",
                                allowed_channels=None, require_mention=True,
                                session_scope="channel"):
    bridge = HiveMindDiscordBridge(
        token=token, key=key, password=password, host=host, port=port,
        self_signed=self_signed, lang=lang, site_id=site_id,
        allowed_channels=allowed_channels, require_mention=require_mention,
        session_scope=session_scope,
    )
    bridge.start()
    return bridge


def _from_file(value, variable):
    """``value`` when given, else the contents of the file named by ``<variable>_FILE``."""
    if value:
        return value
    path = os.environ.get(f"{variable}_FILE")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as error:
        raise click.UsageError(f"{variable}_FILE: cannot read {path}: {error.strerror}") from None


def _channel_ids(ctx, param, values):
    """Channel ids from repeated flags, or from one variable separated by commas or spaces."""
    ids = []
    for value in values or ():
        for item in str(value).replace(",", " ").split():
            try:
                ids.append(int(item))
            except ValueError:
                raise click.BadParameter(f"{item!r} is not a channel id") from None
    return ids


@click.command(epilog="Every option falls back to the environment variable named "
                      "in its help. The token, access key and password also accept "
                      "<VARIABLE>_FILE naming a file that holds the value.")
@click.option("--token", default=None, envvar="DISCORD_BOT_TOKEN",
             help="Discord bot token from the Developer Portal [DISCORD_BOT_TOKEN]")
@click.option("--allowed-channel", "allowed_channels", multiple=True,
             envvar="DISCORD_ALLOWED_CHANNELS", callback=_channel_ids,
             help="Discord text channel id allowed to talk to the bridge "
                  "(repeatable); default: any channel the bot can read, plus DMs "
                  "[DISCORD_ALLOWED_CHANNELS, ids separated by commas]")
@click.option("--require-mention/--no-require-mention", default=True,
             envvar="DISCORD_REQUIRE_MENTION",
             help="in a server channel, forward only messages that mention the bot "
                  "or reply to it (default); --no-require-mention forwards every "
                  "message and needs the privileged message content intent "
                  "[DISCORD_REQUIRE_MENTION]")
@click.option("--session-scope", type=click.Choice(["channel", "person"]),
             default="channel", envvar="DISCORD_SESSION_SCOPE",
             help="one hub conversation per channel (default) or per person in "
                  "each channel [DISCORD_SESSION_SCOPE]")
@click.option("--access-key", "key", default=None, envvar="HIVEMIND_ACCESS_KEY",
             help="HiveMind access key (default: from identity file) [HIVEMIND_ACCESS_KEY]")
@click.option("--password", default=None, envvar="HIVEMIND_PASSWORD",
             help="HiveMind password (default: from identity file) [HIVEMIND_PASSWORD]")
@click.option("--host", default=None, envvar="HIVEMIND_HOST",
             help="HiveMind host, e.g. ws://127.0.0.1 (default: from identity file) "
                  "[HIVEMIND_HOST]")
@click.option("--port", type=int, default=5678, envvar="HIVEMIND_PORT",
             help="HiveMind port (default: 5678) [HIVEMIND_PORT]")
@click.option("--site-id", default="discord", envvar="HIVEMIND_SITE_ID",
             help="this bridge's HiveMind site id (default: discord) [HIVEMIND_SITE_ID]")
@click.option("--self-signed", is_flag=True, envvar="HIVEMIND_SELF_SIGNED",
             help="accept self-signed SSL certificates [HIVEMIND_SELF_SIGNED]")
@click.option("--lang", default="en-us", envvar="HIVEMIND_LANG",
             help="utterance language [HIVEMIND_LANG]")
def main(token, allowed_channels, require_mention, session_scope, key, password,
        host, port, site_id, self_signed, lang):
    """Bridge a Discord bot to a HiveMind node."""
    token = _from_file(token, "DISCORD_BOT_TOKEN")
    if not token:
        raise click.UsageError("a bot token is required: --token, DISCORD_BOT_TOKEN "
                               "or DISCORD_BOT_TOKEN_FILE")
    key = _from_file(key, "HIVEMIND_ACCESS_KEY")
    password = _from_file(password, "HIVEMIND_PASSWORD")
    if host and not host.startswith("ws://") and not host.startswith("wss://"):
        host = "ws://" + host

    LOG.info("bridge starting; press Ctrl-C to stop")
    try:
        connect_discord_to_hivemind(
            token=token, key=key, password=password,
            host=host, port=port, self_signed=self_signed,
            lang=lang, site_id=site_id,
            allowed_channels=list(allowed_channels) or None,
            require_mention=require_mention,
            session_scope=session_scope,
        )
    except KeyboardInterrupt:
        LOG.info("shutting down")


if __name__ == '__main__':
    main()
