"""The bridge's own lines are short and in HIVEMIND_LANG.

The bridge speaks only when the hub cannot: no skill matched, or no answer
came. A Discord message carries no language of its own, so the line is in
HIVEMIND_LANG when there is one for it, else in English.
"""
import time

import pytest

from hivemind_discord_bridge import HiveMindDiscordBridge
from hivemind_discord_bridge.lines import LINES, NO_ANSWER, UNAVAILABLE, bridge_line, language_of

from conftest import DiscordLoop, FakeDiscord, FakeHub, wait_for


@pytest.mark.parametrize("tag, expected", [
    ("fr-FR", "fr"), ("fr_ca", "fr"), ("FR", "fr"), ("pt-br", "pt"), ("en-us", "en"),
    ("", None), (None, None), (42, None),
])
def test_language_of(tag, expected):
    assert language_of(tag) == expected


def test_every_language_has_every_line_and_each_is_short():
    assert LINES.keys() == {"en", "fr", "es", "de", "it", "pt", "nl"}  # as documented
    for lines in LINES.values():
        assert lines.keys() == {NO_ANSWER, UNAVAILABLE}
        assert all(0 < len(line) <= 60 for line in lines.values())


EXPECTED = {
    "en": ("I don't know how to answer that.", "Sorry, I can't answer right now."),
    "fr": ("Je ne sais pas répondre à ça.", "Désolé, je ne peux pas répondre pour le moment."),
    "es": ("No sé cómo responder a eso.", "Lo siento, ahora mismo no puedo responder."),
    "de": ("Darauf weiß ich keine Antwort.", "Entschuldigung, ich kann gerade nicht antworten."),
    "it": ("Non so come rispondere.", "Mi dispiace, al momento non posso rispondere."),
    "pt": ("Não sei responder a isso.", "Desculpe, não consigo responder agora."),
    "nl": ("Daar weet ik geen antwoord op.", "Sorry, ik kan nu niet antwoorden."),
}


@pytest.mark.parametrize("language", sorted(EXPECTED))
def test_each_line_is_really_in_its_language(language):
    """Literal text: replacing a translation with English must fail here."""
    assert (LINES[language][NO_ANSWER], LINES[language][UNAVAILABLE]) == EXPECTED[language]


def test_the_first_language_with_a_line_wins():
    assert bridge_line(NO_ANSWER, "fr-FR") == LINES["fr"][NO_ANSWER]
    assert bridge_line(UNAVAILABLE, "xx", None, "fr-fr") == LINES["fr"][UNAVAILABLE]
    assert bridge_line(NO_ANSWER, "xx-yy") == LINES["en"][NO_ANSWER]
    assert bridge_line(NO_ANSWER) == LINES["en"][NO_ANSWER]


def _no_answer(lang, msg_type="complete_intent_failure"):
    hub = FakeHub()
    fake = FakeDiscord()
    channel = fake.channel(42)
    bridge = HiveMindDiscordBridge(client=hub, discord_client=fake, lang=lang)
    bridge.connect_hivemind()
    loop = DiscordLoop(bridge)
    try:
        hub.send(msg_type, {}, {"channel_id": 42})
        assert wait_for(lambda: channel.sent)
        time.sleep(0.05)
        return [content for _, content, _ in channel.sent]
    finally:
        loop.stop()
        hub.close()


def test_no_answer_follows_hivemind_lang():
    assert _no_answer("fr-fr") == [LINES["fr"][NO_ANSWER]]
    assert _no_answer("ja-jp") == [LINES["en"][NO_ANSWER]]


@pytest.mark.parametrize("msg_type", ["complete_intent_failure", "hive.complete_intent_failure"])
def test_current_and_older_hubs_no_answer_is_handled(msg_type):
    assert _no_answer("en-us", msg_type) == [LINES["en"][NO_ANSWER]]
