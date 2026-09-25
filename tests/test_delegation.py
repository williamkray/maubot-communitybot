"""Tests for sub-space power delegation helpers and parent-leave cleanup.

Covers the pure logic pieces (_apply_user_levels overwrite/max/creator-skip,
_remove_user_from_rooms, _parse_delegation_level, _cleanup_departed_user). The
command handlers and the sync_power_levels event handler are thin orchestration
over these and are exercised by live validation.

Bot is built as Mock(spec=CommunityBot) and methods are invoked unbound as
CommunityBot.<method>(bot, ...), matching the other helper test modules.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from mautrix.types import EventType, JoinRule, Membership
from mautrix.errors import MNotFound

from community.bot import CommunityBot

PARENT = "!parent:example.com"
R1 = "!r1:example.com"
R2 = "!r2:example.com"


def make_bot():
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.mxid = "@bot:example.com"
    bot.client.send_state_event = AsyncMock()
    bot.config = {"parent_room": PARENT, "sleep": 0, "notification_room": None}
    # No creators by default (legacy rooms).
    bot.get_room_version_and_creators = AsyncMock(return_value=("11", []))
    return bot


def pl(users):
    """A power-levels content stand-in supporting .get and item assignment."""
    return {"users": dict(users)}


def store_client(bot, store):
    """Wire client.get_state_event to return power levels from `store` by room."""
    bot.client.get_state_event = AsyncMock(side_effect=lambda room, etype: store[room])


# get_room_name is patched to just echo the room id in all apply/remove tests.
def _patch_room_name():
    return patch(
        "community.bot.common_utils.get_room_name",
        new=AsyncMock(side_effect=lambda c, r, l: r),
    )


class TestParseDelegationLevel:
    def test_default_admin(self):
        assert CommunityBot._parse_delegation_level(None) == 100
        assert CommunityBot._parse_delegation_level("") == 100

    def test_named_levels(self):
        assert CommunityBot._parse_delegation_level("admin") == 100
        assert CommunityBot._parse_delegation_level("ADMIN") == 100
        assert CommunityBot._parse_delegation_level("mod") == 50
        assert CommunityBot._parse_delegation_level("moderator") == 50

    def test_numeric(self):
        assert CommunityBot._parse_delegation_level("75") == 75

    def test_invalid(self):
        assert CommunityBot._parse_delegation_level("bogus") is None


class TestApplyUserLevels:
    @pytest.mark.asyncio
    async def test_overwrite_sets_exact_level(self):
        bot = make_bot()
        store = {R1: pl({"@x:e": 20}), R2: pl({})}
        store_client(bot, store)
        with _patch_room_name():
            success, failed = await CommunityBot._apply_user_levels(
                bot, [R1, R2], {"@x:e": 100}, mode="overwrite"
            )
        assert store[R1]["users"]["@x:e"] == 100
        assert store[R2]["users"]["@x:e"] == 100
        assert set(success) == {R1, R2}
        assert failed == []

    @pytest.mark.asyncio
    async def test_max_never_lowers(self):
        bot = make_bot()
        store = {R1: pl({"@x:e": 100}), R2: pl({"@x:e": 20})}
        store_client(bot, store)
        with _patch_room_name():
            success, failed = await CommunityBot._apply_user_levels(
                bot, [R1, R2], {"@x:e": 50}, mode="max"
            )
        assert store[R1]["users"]["@x:e"] == 100  # not lowered
        assert store[R2]["users"]["@x:e"] == 50  # raised
        assert R1 not in success  # unchanged -> skipped
        assert R2 in success

    @pytest.mark.asyncio
    async def test_skips_creators(self):
        bot = make_bot()
        bot.get_room_version_and_creators = AsyncMock(return_value=("12", ["@x:e"]))
        store = {R1: pl({})}
        store_client(bot, store)
        with _patch_room_name():
            success, failed = await CommunityBot._apply_user_levels(
                bot, [R1], {"@x:e": 100}, mode="max"
            )
        assert "@x:e" not in store[R1]["users"]  # creator not added
        assert success == []
        bot.client.send_state_event.assert_not_called()


class TestRemoveUserFromRooms:
    @pytest.mark.asyncio
    async def test_removes_only_where_present(self):
        bot = make_bot()
        store = {R1: pl({"@x:e": 50}), R2: pl({"@y:e": 50})}
        store_client(bot, store)
        with _patch_room_name():
            success, failed = await CommunityBot._remove_user_from_rooms(
                bot, [R1, R2], "@x:e"
            )
        assert "@x:e" not in store[R1]["users"]
        assert R1 in success
        assert R2 not in success  # unchanged

    @pytest.mark.asyncio
    async def test_keep_level_preserves_parent_grant(self):
        bot = make_bot()
        store = {R1: pl({"@x:e": 100})}
        store_client(bot, store)
        with _patch_room_name():
            await CommunityBot._remove_user_from_rooms(bot, [R1], "@x:e", keep_level=50)
        assert store[R1]["users"]["@x:e"] == 50


class TestCleanupDepartedUser:
    @pytest.mark.asyncio
    async def test_kicks_all_and_strips_including_parent(self):
        bot = make_bot()
        bot.get_space_roomlist = AsyncMock(return_value=[R1, R2])
        bot.client.kick_user = AsyncMock()
        bot._remove_user_from_rooms = AsyncMock(return_value=([], []))

        await CommunityBot._cleanup_departed_user(bot, "@x:e")

        assert bot.client.kick_user.await_count == 2  # both child rooms
        # PL strip called across child rooms plus the parent.
        rooms_arg = bot._remove_user_from_rooms.await_args.args[0]
        assert R1 in rooms_arg and R2 in rooms_arg and PARENT in rooms_arg

    @pytest.mark.asyncio
    async def test_ignores_the_bot_itself(self):
        bot = make_bot()
        bot.get_space_roomlist = AsyncMock(return_value=[R1])
        bot.client.kick_user = AsyncMock()
        bot._remove_user_from_rooms = AsyncMock(return_value=([], []))

        await CommunityBot._cleanup_departed_user(bot, "@bot:example.com")

        bot.client.kick_user.assert_not_called()
        bot._remove_user_from_rooms.assert_not_called()


class TestAutoInviteOnPromotion:
    @pytest.mark.asyncio
    async def test_apply_invites_promoted_user(self):
        bot = make_bot()
        store = {R1: pl({})}
        store_client(bot, store)
        bot._maybe_auto_invite = AsyncMock()
        with _patch_room_name():
            await CommunityBot._apply_user_levels(
                bot, [R1], {"@x:e": 100}, mode="overwrite"
            )
        bot._maybe_auto_invite.assert_awaited_once_with(R1, "@x:e")

    @pytest.mark.asyncio
    async def test_no_invite_below_threshold(self):
        bot = make_bot()  # auto_invite_pl defaults to 50
        store = {R1: pl({})}
        store_client(bot, store)
        bot._maybe_auto_invite = AsyncMock()
        with _patch_room_name():
            await CommunityBot._apply_user_levels(
                bot, [R1], {"@x:e": 20}, mode="overwrite"
            )
        bot._maybe_auto_invite.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_invite_on_demotion(self):
        bot = make_bot()
        store = {R1: pl({"@x:e": 100})}
        store_client(bot, store)
        bot._maybe_auto_invite = AsyncMock()
        with _patch_room_name():
            await CommunityBot._apply_user_levels(
                bot, [R1], {"@x:e": 50}, mode="overwrite"
            )
        # lowered 100 -> 50: changed but not raised, so no invite
        bot._maybe_auto_invite.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_invite_when_invite_only_and_not_member(self):
        bot = make_bot()
        jr = Mock()
        jr.join_rule = JoinRule.INVITE

        async def gse(room, etype, *a):
            if etype == EventType.ROOM_JOIN_RULES:
                return jr
            raise MNotFound("no", "not a member")

        bot.client.get_state_event = AsyncMock(side_effect=gse)
        bot.client.invite_user = AsyncMock()
        await CommunityBot._maybe_auto_invite(bot, R1, "@x:e")
        bot.client.invite_user.assert_awaited_once_with(R1, "@x:e")

    @pytest.mark.asyncio
    async def test_maybe_invite_skips_restricted_room(self):
        bot = make_bot()
        jr = Mock()
        jr.join_rule = JoinRule.RESTRICTED
        bot.client.get_state_event = AsyncMock(return_value=jr)
        bot.client.invite_user = AsyncMock()
        await CommunityBot._maybe_auto_invite(bot, R1, "@x:e")
        bot.client.invite_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_invite_skips_existing_member(self):
        bot = make_bot()
        jr = Mock()
        jr.join_rule = JoinRule.INVITE
        member = Mock()
        member.membership = Membership.JOIN

        async def gse(room, etype, *a):
            if etype == EventType.ROOM_JOIN_RULES:
                return jr
            return member

        bot.client.get_state_event = AsyncMock(side_effect=gse)
        bot.client.invite_user = AsyncMock()
        await CommunityBot._maybe_auto_invite(bot, R1, "@x:e")
        bot.client.invite_user.assert_not_called()
