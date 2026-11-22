"""Tests for command-context gating (issue #5). Bot built via __new__."""

import pytest
from unittest.mock import Mock, AsyncMock

from community.bot import CommunityBot

PARENT = "!parent:example.com"
NOTIF = "!notif:example.com"
CHILD = "!child:example.com"
FOREIGN = "!foreign:other.org"
DM = "!dm:example.com"
BOT = "@bot:example.com"
ADMIN = "@admin:example.com"
MOD = "@mod:example.com"
MEMBER = "@member:example.com"
STRANGER = "@stranger:other.org"

LEVELS = {ADMIN: 100, MOD: 50}
MEMBERS = {ADMIN, MOD, MEMBER}


def _bot(
    roomlist=(CHILD,), lookup_fails=False, dm_members=None, censor=True,
    verifying_rooms=(),
):
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {
        "parent_room": PARENT,
        "notification_room": NOTIF,
        "censor": censor,
        "check_if_human": True,
    }
    bot.log = Mock()
    bot._roomlist_cache = None if lookup_fails else list(roomlist)
    bot.get_space_roomlist = AsyncMock(
        return_value=[] if lookup_fails else list(roomlist)
    )

    async def permitted(user, level, room_id=None):
        return LEVELS.get(user, 0) >= level

    bot.user_permitted = AsyncMock(side_effect=permitted)
    bot.is_user_in_parent_space = AsyncMock(side_effect=lambda u: u in MEMBERS)
    bot.get_verification_state = AsyncMock(
        side_effect=lambda r: {"phrase": "x"} if r in verifying_rooms else None
    )
    client = Mock()
    client.mxid = BOT
    members = {DM: dm_members} if dm_members is not None else {}

    async def joined(room_id):
        if room_id not in members:
            return {}
        return {m: Mock() for m in members[room_id]}

    client.get_joined_members = AsyncMock(side_effect=joined)
    bot.client = client
    return bot


def _evt(sender, room_id):
    evt = Mock()
    evt.sender = sender
    evt.room_id = room_id
    evt.reply = AsyncMock()
    evt.respond = AsyncMock()
    evt.mark_read = AsyncMock()
    return evt


def _dm_bot(sender):
    return _bot(dm_members=[BOT, sender])


# (sender, tier) used in matrix
TIERS = {"admin": ADMIN, "mod": MOD, "member": MEMBER, "nonmember": STRANGER}


class TestCheckCommandContext:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tier", list(TIERS))
    @pytest.mark.parametrize("room", [CHILD, PARENT, NOTIF])
    async def test_community_rooms_allowed_for_everyone(self, tier, room):
        bot = _bot()
        evt = _evt(TIERS[tier], room)
        assert await bot.check_command_context(evt) is True
        evt.reply.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tier,allowed", [("admin", True), ("mod", True), ("member", True), ("nonmember", False)]
    )
    async def test_dm(self, tier, allowed):
        sender = TIERS[tier]
        bot = _dm_bot(sender)
        evt = _evt(sender, DM)
        assert await bot.check_command_context(evt) is allowed
        evt.reply.assert_not_awaited()  # nonmember silent, others allowed

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tier,allowed,replies",
        [("admin", True, False), ("mod", False, True), ("member", False, True), ("nonmember", False, False)],
    )
    async def test_foreign_room(self, tier, allowed, replies):
        bot = _bot()
        evt = _evt(TIERS[tier], FOREIGN)
        assert await bot.check_command_context(evt) is allowed
        assert evt.reply.await_count == (1 if replies else 0)
        if replies:
            assert "community rooms or in a DM" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tier,allowed,replies",
        [("admin", True, False), ("mod", False, True), ("member", False, True), ("nonmember", False, False)],
    )
    async def test_failed_lookup(self, tier, allowed, replies):
        bot = _bot(lookup_fails=True)
        evt = _evt(TIERS[tier], FOREIGN)
        assert await bot.check_command_context(evt) is allowed
        assert evt.reply.await_count == (1 if replies else 0)
        if replies:
            assert "couldn't confirm" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_failed_lookup_still_allows_parent_and_dm(self):
        bot = _bot(lookup_fails=True, dm_members=[BOT, MEMBER])
        assert await bot.check_command_context(_evt(MEMBER, PARENT)) is True
        assert await bot.check_command_context(_evt(MEMBER, DM)) is True

    @pytest.mark.asyncio
    async def test_group_room_with_bot_is_not_dm(self):
        bot = _bot(dm_members=[BOT, MEMBER, STRANGER])
        evt = _evt(MEMBER, DM)
        assert await bot.check_command_context(evt) is False
        evt.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dm_with_other_user_is_not_dm_with_sender(self):
        bot = _bot(dm_members=[BOT, STRANGER])
        evt = _evt(MEMBER, DM)
        assert await bot.check_command_context(evt) is False


class TestVerificationDM:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("sender", [ADMIN, MOD, MEMBER, STRANGER])
    async def test_commands_ignored_silently_in_verification_dm(self, sender):
        bot = _bot(dm_members=[BOT, sender], verifying_rooms=(DM,))
        evt = _evt(sender, DM)
        assert await bot.check_command_context(evt) is False
        evt.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_member_dm_without_pending_verification_allowed(self):
        bot = _bot(dm_members=[BOT, MEMBER])
        assert await bot.check_command_context(_evt(MEMBER, DM)) is True

    @pytest.mark.asyncio
    async def test_community_room_skips_verification_lookup(self):
        bot = _bot(verifying_rooms=(CHILD,))
        assert await bot.check_command_context(_evt(MEMBER, CHILD)) is True
        bot.get_verification_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verification_lookup_error_does_not_block(self):
        bot = _bot(dm_members=[BOT, MEMBER])
        bot.get_verification_state = AsyncMock(side_effect=RuntimeError("db"))
        assert await bot.check_command_context(_evt(MEMBER, DM)) is True


class TestHelpers:
    @pytest.mark.asyncio
    async def test_is_community_room_values(self):
        bot = _bot()
        assert await bot._is_community_room(PARENT) is True
        assert await bot._is_community_room(NOTIF) is True
        assert await bot._is_community_room(CHILD) is True
        assert await bot._is_community_room(FOREIGN) is False
        bot = _bot(lookup_fails=True)
        assert await bot._is_community_room(FOREIGN) is None
        assert await bot._is_community_room(PARENT) is True

    @pytest.mark.asyncio
    async def test_is_community_room_unset_config_does_not_match_empty(self):
        bot = _bot()
        bot.config["notification_room"] = ""
        assert await bot._is_community_room(FOREIGN) is False

    @pytest.mark.asyncio
    async def test_is_dm_with(self):
        bot = _bot(dm_members=[BOT, MEMBER])
        assert await bot._is_dm_with(DM, MEMBER) is True
        assert await bot._is_dm_with(DM, STRANGER) is False
        assert await bot._is_dm_with("!unknown:x", MEMBER) is False

    @pytest.mark.asyncio
    async def test_is_dm_with_only_bot_or_error(self):
        bot = _bot(dm_members=[BOT])
        assert await bot._is_dm_with(DM, MEMBER) is False
        bot.client.get_joined_members = AsyncMock(side_effect=RuntimeError("boom"))
        assert await bot._is_dm_with(DM, MEMBER) is False


class TestDecoratedCommands:
    @pytest.mark.asyncio
    async def test_body_does_not_run_when_denied(self):
        bot = _bot()
        bot.config_manager = Mock()
        bot.config_manager.is_tracking_enabled = Mock(return_value=False)
        evt = _evt(MOD, FOREIGN)
        await CommunityBot.report_all.__mb_func__(bot, evt)
        bot.config_manager.is_tracking_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_body_does_not_run_for_nonmember_silently(self):
        bot = _bot()
        bot.config_manager = Mock()
        evt = _evt(STRANGER, FOREIGN)
        await CommunityBot.report_all.__mb_func__(bot, evt)
        bot.config_manager.is_tracking_enabled.assert_not_called()
        evt.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_body_runs_in_community_room(self):
        bot = _bot()
        bot.config_manager = Mock()
        bot.config_manager.is_tracking_enabled = Mock(return_value=False)
        evt = _evt(MOD, CHILD)
        await CommunityBot.report_all.__mb_func__(bot, evt)
        bot.config_manager.is_tracking_enabled.assert_called_once()
        evt.reply.assert_awaited_once_with("user tracking is disabled")

    @pytest.mark.asyncio
    async def test_admin_body_runs_in_foreign_room(self):
        bot = _bot()
        bot.config_manager = Mock()
        bot.config_manager.is_tracking_enabled = Mock(return_value=False)
        await CommunityBot.report_all.__mb_func__(bot, _evt(ADMIN, FOREIGN))
        bot.config_manager.is_tracking_enabled.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_parent_room_unset(self):
        bot = _bot()
        bot.config["parent_room"] = ""
        bot.check_command_context = AsyncMock(return_value=True)
        evt = _evt(ADMIN, CHILD)
        assert await bot.check_parent_room(evt) is False
        evt.reply.assert_awaited_once()
        assert "initialize" in evt.reply.await_args.args[0]
        bot.check_command_context.assert_not_awaited()


class TestBancheck:
    async def _run(self, bot, evt):
        await CommunityBot.user_bancheck.__mb_func__(bot, evt, "@x:y")

    @pytest.mark.asyncio
    async def test_nonmember_in_community_room_refused(self):
        bot = _bot()
        bot.check_if_banned = AsyncMock(return_value=False)
        evt = _evt(STRANGER, CHILD)
        await self._run(bot, evt)
        bot.check_if_banned.assert_not_awaited()
        assert "community members" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sender", [MEMBER, MOD, ADMIN])
    async def test_member_allowed(self, sender):
        bot = _bot()
        bot.check_if_banned = AsyncMock(return_value=True)
        evt = _evt(sender, CHILD)
        await self._run(bot, evt)
        bot.check_if_banned.assert_awaited_once()
        evt.reply.assert_awaited_once_with("user on banlist: True")

    @pytest.mark.asyncio
    async def test_foreign_room_member_refused(self):
        bot = _bot()
        bot.check_if_banned = AsyncMock()
        await self._run(bot, _evt(MEMBER, FOREIGN))
        bot.check_if_banned.assert_not_awaited()


class TestInitialize:
    def _wired(self, parent=PARENT):
        bot = _bot()
        bot.config["parent_room"] = parent
        bot._initialize_new_space = AsyncMock()
        bot._repair_community = AsyncMock()
        bot._get_space_name = AsyncMock(return_value="Name")
        return bot

    async def _run(self, bot, evt, name=""):
        await CommunityBot.initialize_community.__mb_func__(bot, evt, name)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sender", [MOD, MEMBER])
    async def test_repair_refuses_non_admin_in_community_room(self, sender):
        bot = self._wired()
        evt = _evt(sender, CHILD)
        await self._run(bot, evt)
        bot._repair_community.assert_not_awaited()
        assert "permission" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_repair_nonmember_foreign_silent(self):
        bot = self._wired()
        evt = _evt(STRANGER, FOREIGN)
        await self._run(bot, evt)
        bot._repair_community.assert_not_awaited()
        evt.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repair_admin_runs(self):
        bot = self._wired()
        await self._run(bot, _evt(ADMIN, FOREIGN))
        bot._repair_community.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bootstrap_open_when_parent_unset(self):
        bot = self._wired(parent="")
        await self._run(bot, _evt(STRANGER, FOREIGN), "My Community")
        bot._initialize_new_space.assert_awaited_once()
        bot._repair_community.assert_not_awaited()


class TestMagicLink:
    def _wired(self, dm_room, send_fails=False, dm_members=None):
        bot = _bot(dm_members=dm_members)
        bot.config["event_web_magiclink_ttl"] = 300
        bot._resolve_event_room = AsyncMock(return_value=("!ev:example.com", None))
        bot._get_event_row = AsyncMock(return_value={"x": 1})
        bot._is_user_in_event_room = AsyncMock(return_value=True)
        bot._event_web_base = Mock(return_value="https://web.example.com")
        bot.create_event_session = AsyncMock(return_value="SECRETTOKEN")
        bot._get_dm_room = AsyncMock(return_value=dm_room)
        bot.client.send_text = AsyncMock(
            side_effect=RuntimeError("fail") if send_fails else None
        )
        return bot

    async def _run(self, bot, evt):
        await CommunityBot.event_magiclink.__mb_func__(bot, evt, "")

    def _replies(self, evt):
        return " ".join(c.args[0] for c in evt.reply.await_args_list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dm_room,fails", [(None, False), (DM, True)])
    async def test_link_not_leaked_in_community_room(self, dm_room, fails):
        bot = self._wired(dm_room, send_fails=fails)
        evt = _evt(MEMBER, CHILD)
        await self._run(bot, evt)
        text = self._replies(evt)
        assert "SECRETTOKEN" not in text
        assert "DM" in text

    @pytest.mark.asyncio
    async def test_link_not_leaked_in_group_room_with_bot(self):
        bot = self._wired(None, dm_members=[BOT, MEMBER, MOD])
        evt = _evt(MEMBER, DM)
        await self._run(bot, evt)
        assert "SECRETTOKEN" not in self._replies(evt)

    @pytest.mark.asyncio
    async def test_link_posted_in_real_dm_fallback(self):
        bot = self._wired(None, dm_members=[BOT, MEMBER])
        evt = _evt(MEMBER, DM)
        await self._run(bot, evt)
        assert "SECRETTOKEN" in self._replies(evt)

    @pytest.mark.asyncio
    async def test_link_not_posted_in_community_room_with_only_bot_and_sender(self):
        bot = self._wired(None, dm_members=[BOT, MEMBER])
        bot.get_space_roomlist = AsyncMock(return_value=[DM])
        bot._roomlist_cache = [DM]
        evt = _evt(MEMBER, DM)
        await self._run(bot, evt)
        assert "SECRETTOKEN" not in self._replies(evt)

    @pytest.mark.asyncio
    async def test_link_not_posted_when_lookup_failed(self):
        bot = self._wired(None, dm_members=[BOT, MEMBER])
        bot.get_space_roomlist = AsyncMock(return_value=[])
        bot._roomlist_cache = None
        evt = _evt(MEMBER, DM)
        await self._run(bot, evt)
        assert "SECRETTOKEN" not in self._replies(evt)

    @pytest.mark.asyncio
    async def test_successful_dm_does_not_reply_with_link(self):
        bot = self._wired(DM)
        evt = _evt(MEMBER, CHILD)
        await self._run(bot, evt)
        bot.client.send_text.assert_awaited_once()
        assert "SECRETTOKEN" in bot.client.send_text.await_args.args[1]
        assert "SECRETTOKEN" not in self._replies(evt)


class TestShouldCensor:
    def _msg(self, room):
        m = Mock()
        m.room_id = room
        return m

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "censor,room,matches,expected",
        [
            (True, CHILD, True, True),
            (True, FOREIGN, True, False),
            (True, CHILD, False, False),
            ([FOREIGN], FOREIGN, True, True),
            ([FOREIGN], FOREIGN, False, False),
            (False, CHILD, False, False),
        ],
    )
    async def test_matrix(self, censor, room, matches, expected):
        bot = _bot(censor=censor)
        bot.censor_room = Mock(return_value=matches)
        assert await bot._should_censor(self._msg(room)) is expected

    @pytest.mark.asyncio
    async def test_true_with_failed_lookup_fails_closed(self):
        bot = _bot(lookup_fails=True, censor=True)
        bot.censor_room = Mock(return_value=True)
        assert await bot._should_censor(self._msg(FOREIGN)) is False

    @pytest.mark.asyncio
    async def test_true_with_failed_lookup_still_censors_parent(self):
        bot = _bot(lookup_fails=True, censor=True)
        bot.censor_room = Mock(return_value=True)
        assert await bot._should_censor(self._msg(PARENT)) is True


class TestLeaveCheckIfHuman:
    """user_has_unlimited_power is the first call inside the check_if_human
    branch; stubbing it to True makes it a clean 'branch entered' marker."""

    def _evt(self, room):
        from mautrix.client import SyncStream

        evt = Mock()
        evt.source = SyncStream.TIMELINE
        evt.room_id = room
        evt.state_key = MEMBER
        evt.sender = MEMBER
        return evt

    def _wired(self, check_if_human):
        bot = _bot()
        bot.config["check_if_human"] = check_if_human
        bot.user_has_unlimited_power = AsyncMock(return_value=True)
        return bot

    @pytest.mark.asyncio
    async def test_bool_skipped_in_foreign_room(self):
        bot = self._wired(True)
        await bot.handle_leave_events(self._evt(FOREIGN))
        bot.user_has_unlimited_power.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bool_runs_in_community_room(self):
        bot = self._wired(True)
        await bot.handle_leave_events(self._evt(CHILD))
        bot.user_has_unlimited_power.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_runs_in_listed_foreign_room(self):
        bot = self._wired([FOREIGN])
        await bot.handle_leave_events(self._evt(FOREIGN))
        bot.user_has_unlimited_power.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_skips_unlisted_room(self):
        bot = self._wired([FOREIGN])
        await bot.handle_leave_events(self._evt(CHILD))
        bot.user_has_unlimited_power.assert_not_awaited()
