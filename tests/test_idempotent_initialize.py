"""Tests for re-runnable / idempotent `!community initialize` (repair mode).

Covers:
- `_discover_community_rooms`: alias resolution for all-present, some-missing, and
  MNotFound cases, plus linked-vs-unlinked detection.
- `_repair_community`: create-only-missing decisions, linking existing-but-unlinked
  rooms, and censor config fixes.

The bot cannot be instantiated bare (its constructor needs 10 args), so we build a
`Mock(spec=CommunityBot)` and invoke the methods unbound as
`CommunityBot.<method>(bot, ...)`, exactly like the other helper tests.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from mautrix.errors import MNotFound

from community.bot import CommunityBot

SERVER = "example.com"
SPACE = "!space:example.com"
MOD_ROOM = "!mod:example.com"
WAITING_ROOM = "!waiting:example.com"


def make_bot(**config_overrides):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.mxid = "@bot:example.com"
    bot.client.parse_user_id = Mock(return_value=("bot", SERVER))
    bot.client.resolve_room_alias = AsyncMock()
    bot.client.send_state_event = AsyncMock()
    bot.config = {
        "parent_room": SPACE,
        "community_slug": "tc",
        "use_community_slug": True,
        "censor": False,
        "sleep": 0,
    }
    bot.config.update(config_overrides)
    # Async methods invoked on self
    bot.get_space_roomlist = AsyncMock(return_value=[])
    bot.check_bot_permissions = AsyncMock(return_value=(True, "", {}))
    bot.get_moderators_and_above = AsyncMock(return_value=[])
    bot.create_room = AsyncMock()
    bot.generate_community_slug = Mock(return_value="tc")
    return bot


def make_evt():
    evt = Mock()
    evt.sender = "@admin:example.com"
    evt.respond = AsyncMock(return_value="msg-handle")
    evt.reply = AsyncMock()
    return evt


def resolver(mapping):
    """Return an async resolve_room_alias that maps aliases to room ids or raises MNotFound."""

    async def _resolve(alias):
        if alias in mapping:
            return {"room_id": mapping[alias]}
        raise MNotFound("M_NOT_FOUND", "no such alias")

    return _resolve


# Expected aliases derived the same way the code does.
ALIAS_SPACE = f"#testcommunity:{SERVER}"
ALIAS_MOD = f"#testcommunitymoderators-tc:{SERVER}"
ALIAS_WAITING = f"#testcommunitywaitingroom-tc:{SERVER}"


class TestDiscoverCommunityRooms:
    @pytest.mark.asyncio
    async def test_all_present_and_linked(self):
        bot = make_bot()
        bot.client.resolve_room_alias = AsyncMock(
            side_effect=resolver(
                {
                    ALIAS_SPACE: SPACE,
                    ALIAS_MOD: MOD_ROOM,
                    ALIAS_WAITING: WAITING_ROOM,
                }
            )
        )
        bot.get_space_roomlist = AsyncMock(return_value=[MOD_ROOM, WAITING_ROOM])

        result = await CommunityBot._discover_community_rooms(bot, "Test Community")

        assert result["space"]["room_id"] == SPACE
        assert result["mod_room"]["room_id"] == MOD_ROOM
        assert result["mod_room"]["linked"] is True
        assert result["waiting_room"]["room_id"] == WAITING_ROOM
        assert result["waiting_room"]["linked"] is True

    @pytest.mark.asyncio
    async def test_waiting_room_missing(self):
        bot = make_bot()
        bot.client.resolve_room_alias = AsyncMock(
            side_effect=resolver({ALIAS_SPACE: SPACE, ALIAS_MOD: MOD_ROOM})
        )
        bot.get_space_roomlist = AsyncMock(return_value=[MOD_ROOM])

        result = await CommunityBot._discover_community_rooms(bot, "Test Community")

        assert result["mod_room"]["room_id"] == MOD_ROOM
        assert result["mod_room"]["linked"] is True
        assert result["waiting_room"]["room_id"] is None
        assert result["waiting_room"]["linked"] is False

    @pytest.mark.asyncio
    async def test_existing_but_unlinked(self):
        bot = make_bot()
        bot.client.resolve_room_alias = AsyncMock(
            side_effect=resolver(
                {
                    ALIAS_SPACE: SPACE,
                    ALIAS_MOD: MOD_ROOM,
                    ALIAS_WAITING: WAITING_ROOM,
                }
            )
        )
        # Neither child is a child of the space yet.
        bot.get_space_roomlist = AsyncMock(return_value=[])

        result = await CommunityBot._discover_community_rooms(bot, "Test Community")

        assert result["mod_room"]["room_id"] == MOD_ROOM
        assert result["mod_room"]["linked"] is False
        assert result["waiting_room"]["room_id"] == WAITING_ROOM
        assert result["waiting_room"]["linked"] is False

    @pytest.mark.asyncio
    async def test_mnotfound_treated_as_missing(self):
        bot = make_bot()
        # Everything raises MNotFound.
        bot.client.resolve_room_alias = AsyncMock(side_effect=resolver({}))
        bot.get_space_roomlist = AsyncMock(return_value=[])

        result = await CommunityBot._discover_community_rooms(bot, "Test Community")

        assert result["space"]["room_id"] is None
        assert result["mod_room"]["room_id"] is None
        assert result["waiting_room"]["room_id"] is None

    @pytest.mark.asyncio
    async def test_no_slug_suffix_when_disabled(self):
        bot = make_bot(use_community_slug=False, community_slug="")
        captured = []

        async def _resolve(alias):
            captured.append(alias)
            raise MNotFound("M_NOT_FOUND", "no")

        bot.client.resolve_room_alias = AsyncMock(side_effect=_resolve)
        bot.get_space_roomlist = AsyncMock(return_value=[])

        await CommunityBot._discover_community_rooms(bot, "Test Community")

        assert f"#testcommunity:{SERVER}" in captured
        assert f"#testcommunitymoderators:{SERVER}" in captured
        assert f"#testcommunitywaitingroom:{SERVER}" in captured


class TestRepairCommunity:
    @pytest.mark.asyncio
    async def test_creates_only_missing_waiting_room(self):
        bot = make_bot()
        evt = make_evt()
        # Space + mod present and linked; waiting room missing.
        bot._discover_community_rooms = AsyncMock(
            return_value={
                "space": {"room_id": SPACE, "linked": True},
                "mod_room": {"room_id": MOD_ROOM, "linked": True},
                "waiting_room": {"room_id": None, "linked": False},
            }
        )
        bot.create_room = AsyncMock(return_value=(WAITING_ROOM, ALIAS_WAITING))

        with patch(
            "community.bot.room_creation_utils.add_room_to_space", new=AsyncMock()
        ) as add_link:
            await CommunityBot._repair_community(bot, evt, "Test Community", "msg")

        # Only one room created, and it's the waiting room.
        assert bot.create_room.await_count == 1
        args, kwargs = bot.create_room.await_args
        assert "Waiting Room" in args[0]
        # No linking needed (nothing was unlinked).
        add_link.assert_not_called()
        # Waiting room join rule set + censor updated to include the waiting room.
        bot.client.send_state_event.assert_awaited_once()
        assert bot.config["censor"] == [WAITING_ROOM]

    @pytest.mark.asyncio
    async def test_links_existing_unlinked_rooms_without_creating(self):
        bot = make_bot()
        evt = make_evt()
        bot._discover_community_rooms = AsyncMock(
            return_value={
                "space": {"room_id": SPACE, "linked": True},
                "mod_room": {"room_id": MOD_ROOM, "linked": False},
                "waiting_room": {"room_id": WAITING_ROOM, "linked": False},
            }
        )

        with patch(
            "community.bot.room_creation_utils.add_room_to_space", new=AsyncMock()
        ) as add_link:
            await CommunityBot._repair_community(bot, evt, "Test Community", "msg")

        # Nothing created; both existing rooms linked.
        bot.create_room.assert_not_called()
        assert add_link.await_count == 2
        linked_ids = {call.args[2] for call in add_link.await_args_list}
        assert linked_ids == {MOD_ROOM, WAITING_ROOM}
        # Censor gets the pre-existing waiting room too.
        assert bot.config["censor"] == [WAITING_ROOM]

    @pytest.mark.asyncio
    async def test_nothing_to_do_when_complete(self):
        bot = make_bot(censor=[WAITING_ROOM])
        evt = make_evt()
        bot._discover_community_rooms = AsyncMock(
            return_value={
                "space": {"room_id": SPACE, "linked": True},
                "mod_room": {"room_id": MOD_ROOM, "linked": True},
                "waiting_room": {"room_id": WAITING_ROOM, "linked": True},
            }
        )

        with patch(
            "community.bot.room_creation_utils.add_room_to_space", new=AsyncMock()
        ) as add_link:
            await CommunityBot._repair_community(bot, evt, "Test Community", "msg")

        bot.create_room.assert_not_called()
        add_link.assert_not_called()
        bot.client.send_state_event.assert_not_called()
        # Final response indicates completeness.
        final = evt.respond.await_args
        assert "complete" in final.args[0].lower()

    @pytest.mark.asyncio
    async def test_bails_when_bot_lacks_permissions(self):
        bot = make_bot()
        evt = make_evt()
        bot.check_bot_permissions = AsyncMock(
            return_value=(False, "Bot is not a member of this room", {})
        )

        await CommunityBot._repair_community(bot, evt, "Test Community", "msg")

        bot._discover_community_rooms.assert_not_called()
        bot.create_room.assert_not_called()
        msg = evt.respond.await_args.args[0]
        assert "Cannot repair" in msg

    @pytest.mark.asyncio
    async def test_regenerates_missing_slug(self):
        bot = make_bot(community_slug="")
        evt = make_evt()
        bot._discover_community_rooms = AsyncMock(
            return_value={
                "space": {"room_id": SPACE, "linked": True},
                "mod_room": {"room_id": MOD_ROOM, "linked": True},
                "waiting_room": {"room_id": WAITING_ROOM, "linked": True},
            }
        )

        with patch(
            "community.bot.room_creation_utils.add_room_to_space", new=AsyncMock()
        ):
            await CommunityBot._repair_community(bot, evt, "Test Community", "msg")

        bot.generate_community_slug.assert_called_once_with("Test Community")
        assert bot.config["community_slug"] == "tc"

    @pytest.mark.asyncio
    async def test_aborts_when_missing_room_creation_fails(self):
        bot = make_bot()
        evt = make_evt()
        bot._discover_community_rooms = AsyncMock(
            return_value={
                "space": {"room_id": SPACE, "linked": True},
                "mod_room": {"room_id": None, "linked": False},
                "waiting_room": {"room_id": None, "linked": False},
            }
        )
        # create_room returns None (failure) for the mod room.
        bot.create_room = AsyncMock(return_value=None)

        with patch(
            "community.bot.room_creation_utils.add_room_to_space", new=AsyncMock()
        ):
            await CommunityBot._repair_community(bot, evt, "Test Community", "msg")

        # Only attempted the first (mod) room, then bailed before the waiting room.
        assert bot.create_room.await_count == 1
        msg = evt.respond.await_args.args[0]
        assert "Failed to create" in msg
