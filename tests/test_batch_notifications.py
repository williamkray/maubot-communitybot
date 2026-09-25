"""Tests for batched community-action notifications + per-event suppression."""

import pytest
from unittest.mock import Mock, AsyncMock

from mautrix.client import SyncStream

from community.bot import CommunityBot


def make_bot(notification_room="!notif:example.com"):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.mxid = "@bot:example.com"
    bot.client.send_message_event = AsyncMock(return_value="$evt")
    bot.client.send_notice = AsyncMock(return_value="$evt")
    bot.config = {"notification_room": notification_room}
    return bot


class TestSummarizeRooms:
    def test_empty(self):
        assert CommunityBot._summarize_rooms([]) == "no rooms"

    def test_short_list(self):
        assert CommunityBot._summarize_rooms(["A", "B", "C"]) == "A, B, C"

    def test_truncates_long_list(self):
        names = [f"R{i}" for i in range(10)]
        out = CommunityBot._summarize_rooms(names)
        assert out.startswith("R0, R1, R2, R3, R4, R5")
        assert "and 4 more" in out


class TestPostNotification:
    @pytest.mark.asyncio
    async def test_no_room_returns_none(self):
        bot = make_bot(notification_room=None)
        result = await CommunityBot._post_notification(bot, "hi")
        assert result is None
        bot.client.send_message_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_posts_notice(self):
        bot = make_bot()
        result = await CommunityBot._post_notification(bot, "hello")
        assert result == "$evt"
        bot.client.send_message_event.assert_awaited_once()
        room, etype, content = bot.client.send_message_event.await_args.args
        assert room == "!notif:example.com"
        assert content.formatted_body == "hello"

    @pytest.mark.asyncio
    async def test_edit_sets_relation(self):
        bot = make_bot()
        await CommunityBot._post_notification(bot, "updated", edit="$orig")
        content = bot.client.send_message_event.await_args.args[2]
        # set_edit populates the m.replace relation
        assert content.relates_to is not None


class TestSuppressBotInitiated:
    @pytest.mark.asyncio
    async def test_bot_sender_suppressed(self):
        bot = make_bot()
        bot.get_space_roomlist = AsyncMock(return_value=["!r:example.com"])
        evt = Mock()
        evt.source = SyncStream.TIMELINE  # not a state-sync replay
        evt.sender = "@bot:example.com"  # == bot.mxid -> suppress
        evt.state_key = "@victim:example.com"
        evt.room_id = "!r:example.com"
        await CommunityBot.send_membership_notification(bot, evt, "ban_notification_message", actor_id="@bot:example.com")
        bot.client.send_notice.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_sender_notifies(self):
        bot = make_bot()
        bot.get_space_roomlist = AsyncMock(return_value=["!r:example.com"])
        bot.config["kick_notification_message"] = "User {user} kicked from {room} by {actor}"
        # room name lookup
        name = Mock(); name.__getitem__ = lambda self, k: "GeneralChat"
        bot.client.get_state_event = AsyncMock(return_value={"name": "GeneralChat"})
        evt = Mock()
        evt.source = SyncStream.TIMELINE
        evt.sender = "@mod:example.com"  # human, not the bot
        evt.state_key = "@victim:example.com"
        evt.room_id = "!r:example.com"
        await CommunityBot.send_membership_notification(bot, evt, "kick_notification_message", actor_id="@mod:example.com")
        bot.client.send_notice.assert_awaited_once()
