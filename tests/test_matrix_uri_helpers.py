import html
from types import SimpleNamespace

import pytest

from community.bot import CommunityBot, DEFAULT_ROOM_PILL_PREFIX, DEFAULT_USER_PILL_PREFIX


@pytest.fixture()
def bot() -> CommunityBot:
    plugin = CommunityBot.__new__(CommunityBot)
    plugin.config = {}
    return plugin


def test_user_uri_helper_strips_at_and_uses_chat_action(bot: CommunityBot) -> None:
    assert bot._matrix_user_uri("@alice:example.org") == "matrix:u/alice:example.org?action=chat"


def test_room_uri_helper_prefers_alias(bot: CommunityBot) -> None:
    assert bot._matrix_room_uri("!roomid:example.org", "#general:example.org") == "matrix:r/general:example.org"


def test_room_uri_helper_falls_back_to_room_id_without_bang(bot: CommunityBot) -> None:
    assert bot._matrix_room_uri("!roomid:example.org", None) == "matrix:roomid/roomid:example.org"


def test_event_uri_helper_strips_prefixes(bot: CommunityBot) -> None:
    assert bot._matrix_event_uri("!roomid:example.org", "$eventid") == "matrix:roomid/roomid:example.org/e/eventid"


def test_format_user_pill_uses_clean_default_prefix(bot: CommunityBot) -> None:
    plain, formatted = bot._format_user_pill("@alice:example.org", "Alice")
    assert plain == f"{DEFAULT_USER_PILL_PREFIX}Alice"
    assert 'href="matrix:u/alice:example.org?action=chat"' in formatted
    assert ">Alice<" in formatted


def test_format_room_pill_uses_alias_when_available(bot: CommunityBot) -> None:
    plain, formatted = bot._format_room_pill("!roomid:example.org", "General", "#general:example.org")
    assert plain == f"{DEFAULT_ROOM_PILL_PREFIX}General"
    assert formatted == '<a href="matrix:r/general:example.org">General</a>'


def test_format_room_pill_falls_back_to_room_id(bot: CommunityBot) -> None:
    plain, formatted = bot._format_room_pill("!roomid:example.org", "General", None)
    assert plain == f"{DEFAULT_ROOM_PILL_PREFIX}General"
    assert formatted == '<a href="matrix:roomid/roomid:example.org">General</a>'


def test_format_user_pill_escapes_displayname(bot: CommunityBot) -> None:
    plain, formatted = bot._format_user_pill("@alice:example.org", '<Admin & Ops>')
    assert plain == f"{DEFAULT_USER_PILL_PREFIX}<Admin & Ops>"
    # Keep this broad enough to avoid coupling to quote style.
    assert "matrix:u/alice:example.org?action=chat" in formatted
    assert html.escape('<Admin & Ops>') in formatted
