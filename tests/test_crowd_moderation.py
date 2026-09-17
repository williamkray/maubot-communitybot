"""Tests for emoji-reaction crowd moderation (Phase 3)."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from mautrix.types import ReactionEvent, RelationType

from community.bot import CommunityBot
from community.helpers import database_utils

ROOM = "!room:example.com"
NOTIFY = "!notify:example.com"
TARGET = "$evt:example.com"
BOT = "@bot:example.com"


def make_bot(**cfg):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.database = Mock()
    bot.client = Mock()
    bot.client.mxid = BOT
    bot.client.get_joined_members = AsyncMock(
        return_value={BOT: {}, "@a:example.com": {}, "@b:example.com": {}}
    )
    bot.client.redact = AsyncMock()
    bot.client.send_notice = AsyncMock()
    # trivial helpers stubbed with faithful behavior
    bot._get_room_name = AsyncMock(return_value="Cool Room")
    bot._matrix_to_link = Mock(
        side_effect=lambda target, label=None: f'<a href="https://matrix.to/#/{target}">{label or target}</a>'
    )
    config = {
        "notification_room": NOTIFY,
        "report_emojis": ["🚩", "⚠️"],
        "auto_redact_majority": False,
    }
    config.update(cfg)
    bot.config = config
    return bot


def make_evt(key="🚩", rel_type=RelationType.ANNOTATION, sender="@a:example.com"):
    evt = Mock(spec=ReactionEvent)
    evt.sender = sender
    evt.room_id = ROOM
    evt.timestamp = 1000
    evt.content = Mock()
    relates_to = Mock()
    relates_to.rel_type = rel_type
    relates_to.key = key
    relates_to.event_id = TARGET
    evt.content.relates_to = relates_to
    return evt


async def call(bot, evt):
    await CommunityBot.handle_report_reaction(bot, evt)


class TestCrowdModeration:
    @pytest.mark.asyncio
    async def test_first_report_notifies_moderators(self):
        bot = make_bot()
        with patch.object(database_utils, "record_message_report", AsyncMock(return_value=True)), \
             patch.object(database_utils, "count_message_reports", AsyncMock(return_value=1)):
            await call(bot, make_evt())
        bot.client.send_notice.assert_awaited_once()
        _, kwargs = bot.client.send_notice.call_args
        assert "Message reported" in kwargs["html"]
        bot.client.redact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_second_report_does_not_renotify(self):
        bot = make_bot()
        with patch.object(database_utils, "record_message_report", AsyncMock(return_value=True)), \
             patch.object(database_utils, "count_message_reports", AsyncMock(return_value=2)):
            await call(bot, make_evt(sender="@b:example.com"))
        # count > 1 and auto-redact disabled -> no notice
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_report_emoji_ignored(self):
        bot = make_bot()
        with patch.object(database_utils, "record_message_report", AsyncMock()) as rec:
            await call(bot, make_evt(key="👍"))
        rec.assert_not_awaited()
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_annotation_ignored(self):
        bot = make_bot()
        with patch.object(database_utils, "record_message_report", AsyncMock()) as rec:
            await call(bot, make_evt(rel_type=RelationType.REPLACE))
        rec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_report_from_same_user(self):
        bot = make_bot()
        with patch.object(database_utils, "record_message_report", AsyncMock(return_value=False)), \
             patch.object(database_utils, "count_message_reports", AsyncMock()) as cnt:
            await call(bot, make_evt())
        cnt.assert_not_awaited()
        bot.client.send_notice.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_notification_room_disables(self):
        bot = make_bot(notification_room="")
        with patch.object(database_utils, "record_message_report", AsyncMock()) as rec:
            await call(bot, make_evt())
        rec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_report_emojis_disables(self):
        bot = make_bot(report_emojis=[])
        with patch.object(database_utils, "record_message_report", AsyncMock()) as rec:
            await call(bot, make_evt())
        rec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_redact_on_majority(self):
        # 2 human members (a, b); 2 reports > 1 -> redact
        bot = make_bot(auto_redact_majority=True)
        with patch.object(database_utils, "record_message_report", AsyncMock(return_value=True)), \
             patch.object(database_utils, "count_message_reports", AsyncMock(return_value=2)), \
             patch.object(database_utils, "clear_message_reports", AsyncMock()) as clear:
            await call(bot, make_evt(sender="@b:example.com"))
        bot.client.redact.assert_awaited_once()
        clear.assert_awaited_once()
        _, kwargs = bot.client.send_notice.call_args
        assert "auto-redacted" in kwargs["html"].lower()

    @pytest.mark.asyncio
    async def test_no_redact_below_majority(self):
        # 2 human members; only 1 report -> not a majority (1 > 1 is False)
        bot = make_bot(auto_redact_majority=True)
        with patch.object(database_utils, "record_message_report", AsyncMock(return_value=True)), \
             patch.object(database_utils, "count_message_reports", AsyncMock(return_value=1)), \
             patch.object(database_utils, "clear_message_reports", AsyncMock()) as clear:
            await call(bot, make_evt())
        bot.client.redact.assert_not_awaited()
        clear.assert_not_awaited()
        # first report still surfaces to moderators
        _, kwargs = bot.client.send_notice.call_args
        assert "Message reported" in kwargs["html"]
