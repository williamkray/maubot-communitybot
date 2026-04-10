"""Tests for notification and greeting template rendering."""

from unittest.mock import AsyncMock, Mock

import pytest
from mautrix.types import EventType, RoomID

from community.bot import CommunityBot


@pytest.fixture
def bot():
    bot = CommunityBot.__new__(CommunityBot)
    bot.client = Mock()
    bot.config = {"matrix_to_base_url": "https://matrix.to"}
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

    assert plain == "Alice / @alice:example.org / https://matrix.to/#/@alice:example.org"
    assert "Alice / @alice:example.org / " in html
    assert '<a href=' in html
    assert '>Alice</a>' in html
