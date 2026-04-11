"""Tests for notification and greeting template rendering."""

from unittest.mock import AsyncMock, Mock

import pytest
from mautrix.types import EventType, RoomID

from community.bot import CommunityBot


@pytest.fixture
def bot():
    bot = CommunityBot.__new__(CommunityBot)
    bot.client = Mock()
    bot.config = {
        "matrix_to_base_url": "https://matrix.to",
        "user_pill_prefix": "@",
        "room_pill_prefix": "#",
    }
    return bot


@pytest.mark.asyncio
async def test_get_user_display_name_uses_member_displayname(bot):
    member_state = Mock()
    member_state.displayname = "Alice"
    bot.client.get_state_event = AsyncMock(return_value=member_state)

    result = await bot._get_user_display_name(RoomID("!room:example.org"), "@alice:example.org")

    assert result == "Alice"
    bot.client.get_state_event.assert_awaited_once_with(
        RoomID("!room:example.org"),
        EventType.ROOM_MEMBER,
        state_key="@alice:example.org",
    )


@pytest.mark.asyncio
async def test_get_user_display_name_falls_back_to_localpart(bot):
    bot.client.get_state_event = AsyncMock(side_effect=Exception("missing"))
    bot.client.parse_user_id = Mock(return_value=("alice", "example.org"))

    result = await bot._get_user_display_name(RoomID("!room:example.org"), "@alice:example.org")

    assert result == "alice"


def test_render_message_template_supports_user_id_and_user_link(bot):
    plain, html = bot._render_message_template(
        "{user} / {user_id} / {user_link}",
        "@alice:example.org",
        "Alice",
        "!room:example.org",
        "General",
    )

    assert plain == "@Alice / @alice:example.org / https://matrix.to/#/@alice:example.org"
    assert "@Alice / @alice:example.org / " in html
    assert '<a href=' in html
    assert '>Alice</a>' in html


def test_render_message_template_uses_configurable_user_and_room_pill_prefixes(bot):
    bot.config["user_pill_prefix"] = ""
    bot.config["room_pill_prefix"] = ""

    plain, html = bot._render_message_template(
        "{user} has joined {room}.",
        "@alice:example.org",
        "Alice",
        "!room:example.org",
        "General",
    )

    assert plain == "Alice has joined General."
    assert "<a href='matrix:u/alice:example.org?action=chat'>Alice</a>" in html
    assert "<a href='matrix:roomid/room:example.org'>General</a>" in html


def test_render_message_template_defaults_to_prefixed_user_and_room_pills(bot):
    plain, html = bot._render_message_template(
        "{user} has joined {room}.",
        "@alice:example.org",
        "Alice",
        "!room:example.org",
        "General",
    )

    assert plain == "@Alice has joined #General."
    assert "<a href='matrix:u/alice:example.org?action=chat'>@Alice</a>" in html
    assert "<a href='matrix:roomid/room:example.org'>#General</a>" in html


def test_matrix_uri_helpers_are_consistent():
    from community.bot import CommunityBot

    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {}

    assert bot._matrix_user_uri("@alice:example.org") == "matrix:u/alice:example.org?action=chat"
    assert bot._matrix_room_uri("!roomid:example.org", "#general:example.org") == "matrix:r/general:example.org"
    assert bot._matrix_room_uri("!roomid:example.org", None) == "matrix:roomid/roomid:example.org"
    assert bot._matrix_event_uri("!roomid:example.org", "$eventid") == "matrix:roomid/roomid:example.org/e/eventid"
