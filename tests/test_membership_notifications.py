"""Tests for leave/kick/ban membership notifications (Phase 2)."""

import pytest
from unittest.mock import Mock, AsyncMock
from mautrix.types import StateEvent
from mautrix.client import SyncStream

from community.bot import CommunityBot

ROOM = "!room:example.com"
NOTIFY = "!notify:example.com"


def make_bot(**overrides):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.get_state_event = AsyncMock(return_value={"name": "Cool Room"})
    bot.client.send_notice = AsyncMock()
    bot.get_space_roomlist = AsyncMock(return_value=[ROOM])
    bot.config = {
        "notification_room": NOTIFY,
        "leave_notification_message": "User <code>{user}</code> has left <code>{room}</code>.",
        "kick_notification_message": "User <code>{user}</code> was kicked from <code>{room}</code> by <code>{actor}</code>.",
        "ban_notification_message": "User <code>{user}</code> was banned from <code>{room}</code> by <code>{actor}</code>.",
    }
    bot.config.update(overrides.pop("config", {}))
    return bot


def make_evt(source=SyncStream.TIMELINE, room_id=ROOM, state_key="@bad:example.com",
             sender="@mod:example.com"):
    evt = Mock(spec=StateEvent)
    evt.source = source
    evt.room_id = room_id
    evt.state_key = state_key
    evt.sender = sender
    return evt


class TestMembershipNotifications:
    @pytest.mark.asyncio
    async def test_kick_includes_actor(self):
        bot = make_bot()
        evt = make_evt()
        await CommunityBot.send_membership_notification(
            bot, evt, "kick_notification_message", actor_id=evt.sender
        )
        bot.client.send_notice.assert_awaited_once()
        _, kwargs = bot.client.send_notice.call_args
        html = kwargs["html"]
        assert "@bad:example.com" in html
        assert "Cool Room" in html
        assert "@mod:example.com" in html

    @pytest.mark.asyncio
    async def test_leave_has_no_actor(self):
        bot = make_bot()
        evt = make_evt(sender="@bad:example.com")  # voluntary leave: sender == user
        await CommunityBot.send_membership_notification(
            bot, evt, "leave_notification_message"
        )
        bot.client.send_notice.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_rooms_outside_space(self):
        bot = make_bot()
        evt = make_evt(room_id="!other:example.com")
        await CommunityBot.send_membership_notification(
            bot, evt, "leave_notification_message"
        )
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_notification_room(self):
        bot = make_bot(config={"notification_room": ""})
        evt = make_evt()
        await CommunityBot.send_membership_notification(
            bot, evt, "leave_notification_message"
        )
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_state_sync_events(self):
        bot = make_bot()
        evt = make_evt(source=SyncStream.STATE)
        await CommunityBot.send_membership_notification(
            bot, evt, "leave_notification_message"
        )
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blank_template_disables_notification(self):
        bot = make_bot(config={"ban_notification_message": ""})
        evt = make_evt()
        await CommunityBot.send_membership_notification(
            bot, evt, "ban_notification_message", actor_id=evt.sender
        )
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_room_id_without_name(self):
        bot = make_bot()
        bot.client.get_state_event = AsyncMock(side_effect=Exception("no name"))
        evt = make_evt()
        await CommunityBot.send_membership_notification(
            bot, evt, "leave_notification_message"
        )
        _, kwargs = bot.client.send_notice.call_args
        assert ROOM in kwargs["html"]
