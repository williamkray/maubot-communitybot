"""create_space applies the community slug to the alias only when use_slug=True.

Parent space (initialize) stays slug-less; subspaces (space create) get the
'-<slug>' suffix, matching room aliases.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from community.bot import CommunityBot

SERVER = "example.com"


def make_bot(**cfg):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.mxid = "@bot:example.com"
    bot.client.parse_user_id = Mock(return_value=("bot", SERVER))
    bot.client.create_room = AsyncMock(return_value="!new:example.com")
    bot.client.get_state = AsyncMock(return_value=[])
    bot.client.send_state_event = AsyncMock()
    bot.validate_room_alias = AsyncMock(return_value=True)  # alias available
    bot.get_room_version_and_creators = AsyncMock(return_value=("11", []))
    bot.is_modern_room_version = Mock(return_value=False)
    bot.config = {
        "invitees": [],
        "room_version": "11",
        "use_community_slug": True,
        "community_slug": "tc",
    }
    bot.config.update(cfg)
    return bot


async def _alias_used(bot):
    return bot.client.create_room.await_args.kwargs["alias_localpart"]


@pytest.mark.asyncio
async def test_subspace_gets_slug_suffix():
    bot = make_bot()
    _id, alias = await CommunityBot.create_space(bot, "Projects", None, use_slug=True)
    assert await _alias_used(bot) == "projects-tc"
    assert alias == "#projects-tc:example.com"


@pytest.mark.asyncio
async def test_parent_space_has_no_slug():
    bot = make_bot()
    _id, alias = await CommunityBot.create_space(bot, "Projects", None, use_slug=False)
    assert await _alias_used(bot) == "projects"
    assert alias == "#projects:example.com"


@pytest.mark.asyncio
async def test_no_suffix_when_slug_disabled():
    bot = make_bot(use_community_slug=False)
    await CommunityBot.create_space(bot, "Projects", None, use_slug=True)
    assert await _alias_used(bot) == "projects"


@pytest.mark.asyncio
async def test_no_suffix_when_slug_empty():
    bot = make_bot(community_slug="")
    await CommunityBot.create_space(bot, "Projects", None, use_slug=True)
    assert await _alias_used(bot) == "projects"
