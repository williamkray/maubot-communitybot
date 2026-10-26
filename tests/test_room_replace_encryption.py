"""Tests for how `room replace` picks the replacement room's encryption."""

import pytest
from unittest.mock import Mock, AsyncMock

from mautrix.errors import MNotFound
from mautrix.types import EventType

from community.bot import CommunityBot

ROOM = "!old:example.com"


def _make_bot(get_state_event):
    bot = CommunityBot.__new__(CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.get_state_event = AsyncMock(side_effect=get_state_event)
    return bot


async def _encrypted(room_id, event_type):
    assert event_type == EventType.ROOM_ENCRYPTION
    return Mock(algorithm="m.megolm.v1.aes-sha2")


async def _unencrypted(room_id, event_type):
    raise MNotFound(404, "Event not found.")


async def _broken(room_id, event_type):
    raise Exception("connection reset")


@pytest.mark.asyncio
async def test_keeps_old_room_encrypted():
    assert await _make_bot(_encrypted)._replacement_encryption(ROOM, False, False) is True


@pytest.mark.asyncio
async def test_keeps_old_room_unencrypted():
    assert await _make_bot(_unencrypted)._replacement_encryption(ROOM, False, False) is False


@pytest.mark.asyncio
async def test_unencrypted_flag_overrides_encrypted_room():
    bot = _make_bot(_encrypted)
    assert await bot._replacement_encryption(ROOM, False, True) is False
    bot.client.get_state_event.assert_not_called()


@pytest.mark.asyncio
async def test_encrypted_flag_overrides_unencrypted_room():
    assert await _make_bot(_unencrypted)._replacement_encryption(ROOM, True, False) is True


@pytest.mark.asyncio
async def test_unreadable_state_falls_back_to_config():
    assert await _make_bot(_broken)._replacement_encryption(ROOM, False, False) is None
