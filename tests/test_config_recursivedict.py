"""Regression tests: the live plugin config is a mautrix RecursiveDict whose
`.get(key)` REQUIRES a default argument (unlike a plain dict). Handlers that read
config with a bare `.get(key)` crash in production with:

    TypeError: RecursiveDict.get() missing 1 required positional argument: 'default_value'

These tests drive the config-reading handlers with a real RecursiveDict so any
future defaultless `.get` is caught here rather than in production logs.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from mautrix.types import ReactionEvent, StateEvent, RelationType
from mautrix.client import SyncStream
from mautrix.util.config import RecursiveDict
from ruamel.yaml.comments import CommentedMap

from community.bot import CommunityBot
from community.helpers import database_utils

ROOM = "!room:example.com"
NOTIFY = "!notify:example.com"
TARGET = "$evt:example.com"
BOT = "@bot:example.com"


def rconfig(**kv):
    rd = RecursiveDict(CommentedMap())
    for k, v in kv.items():
        rd[k] = v
    return rd


def _bot(cfg):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.database = Mock()
    bot.client = Mock()
    bot.client.mxid = BOT
    bot.client.send_notice = AsyncMock()
    bot.client.redact = AsyncMock()
    bot.client.get_joined_members = AsyncMock(return_value={BOT: {}, "@a:example.com": {}})
    bot.client.get_state_event = AsyncMock(return_value={"name": "Room"})
    bot.get_space_roomlist = AsyncMock(return_value=[ROOM])
    bot._get_room_name = AsyncMock(return_value="Room")
    bot._matrix_to_link = Mock(side_effect=lambda t, l=None: f"<a>{l or t}</a>")
    bot.config = cfg
    return bot


def _reaction(key="🚩"):
    evt = Mock(spec=ReactionEvent)
    evt.sender = "@a:example.com"
    evt.room_id = ROOM
    evt.timestamp = 1
    evt.content = Mock()
    rel = Mock()
    rel.rel_type = RelationType.ANNOTATION
    rel.key = key
    rel.event_id = TARGET
    evt.content.relates_to = rel
    return evt


def _state_evt():
    evt = Mock(spec=StateEvent)
    evt.source = SyncStream.TIMELINE
    evt.room_id = ROOM
    evt.state_key = "@bad:example.com"
    evt.sender = "@mod:example.com"
    return evt


class TestRecursiveDictConfig:
    @pytest.mark.asyncio
    async def test_report_reaction_with_recursivedict_config(self):
        cfg = rconfig(
            notification_room=NOTIFY,
            report_emojis=["🚩", "⚠️"],
            auto_redact_majority=False,
        )
        bot = _bot(cfg)
        with patch.object(database_utils, "record_message_report", AsyncMock(return_value=True)), \
             patch.object(database_utils, "count_message_reports", AsyncMock(return_value=1)):
            # must not raise RecursiveDict.get TypeError
            await CommunityBot.handle_report_reaction(bot, _reaction("🚩"))
        bot.client.send_notice.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_membership_notification_with_recursivedict_config(self):
        cfg = rconfig(
            notification_room=NOTIFY,
            kick_notification_message="User <code>{user}</code> kicked from <code>{room}</code> by <code>{actor}</code>.",
        )
        bot = _bot(cfg)
        await CommunityBot.send_membership_notification(
            bot, _state_evt(), "kick_notification_message", actor_id="@mod:example.com"
        )
        bot.client.send_notice.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_reaction_empty_report_emojis_default(self):
        # report_emojis absent entirely -> .get must supply [] default, not crash
        cfg = rconfig(notification_room=NOTIFY)
        bot = _bot(cfg)
        with patch.object(database_utils, "record_message_report", AsyncMock()) as rec:
            await CommunityBot.handle_report_reaction(bot, _reaction("🚩"))
        rec.assert_not_awaited()  # no report emojis configured -> ignored, no crash
