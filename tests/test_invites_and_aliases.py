"""Tests for command aliases, membership/invite helpers, `user invite` and
`room invite`. The bot is built with __new__ (bypassing the constructor)."""

import pytest
from unittest.mock import Mock, AsyncMock

from mautrix.errors import MNotFound
from mautrix.types import EventType, JoinRule, Membership

from community.bot import CommunityBot

PARENT = "!parent:example.com"
MOD = "@mod:example.com"
USER = "@user:example.com"
TARGET = "@target:example.com"


def _bot(aliases=None, memberships=None, invite_rooms=None, mod=True, join_rules=None):
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {"parent_room": PARENT}
    if aliases is not None:
        bot.config["command_aliases"] = aliases
    if invite_rooms is not None:
        bot.config["invite_rooms"] = invite_rooms
    bot.log = Mock()
    memberships = memberships or {}
    # room_id -> join rules content; default is a public room
    join_rules = join_rules or {}

    async def get_state_event(room_id, event_type, state_key=""):
        if event_type == EventType.ROOM_JOIN_RULES:
            jr = join_rules.get(room_id, _jr(JoinRule.PUBLIC))
            if isinstance(jr, Exception):
                raise jr
            return jr
        m = memberships.get((room_id, state_key))
        if m is None:
            raise MNotFound("not found", 404)
        return Mock(membership=m)

    client = Mock()
    client.get_state_event = AsyncMock(side_effect=get_state_event)
    client.invite_user = AsyncMock()
    client.mxid = "@bot:example.com"
    bot.client = client
    bot.check_if_banned = AsyncMock(return_value=False)
    bot.user_permitted = AsyncMock(return_value=mod)
    bot.get_space_roomlist = AsyncMock(return_value=["!child:example.com"])
    bot._post_notification = AsyncMock()
    return bot


def _jr(rule, allow_rooms=()):
    return Mock(
        join_rule=rule, allow=[Mock(room_id=r) for r in allow_rooms]
    )


def _evt(sender=MOD, room_id="!child:example.com"):
    evt = Mock()
    evt.sender = sender
    evt.room_id = room_id
    evt.mark_read = AsyncMock()
    evt.reply = AsyncMock()
    return evt


@pytest.fixture(autouse=True)
def _room_names(monkeypatch):
    async def get_room_name(client, room_id, log):
        return f"name-{room_id}"

    monkeypatch.setattr("community.helpers.common_utils.get_room_name", get_room_name)


def _reply_text(evt):
    return "\n".join(c.args[0] for c in evt.reply.await_args_list)


# --- aliases -----------------------------------------------------------------


class TestAliases:
    def test_community_always_matches(self):
        assert CommunityBot._is_community_command(_bot(aliases=[]), "community")

    def test_alias_match(self):
        assert CommunityBot._is_community_command(_bot(aliases=["cb"]), "cb")

    def test_non_match(self):
        assert not CommunityBot._is_community_command(_bot(aliases=["cb"]), "other")

    def test_alias_case_insensitive(self):
        assert CommunityBot._is_community_command(_bot(aliases=["CB"]), "cb")

    def test_alias_bang_prefix_stripped(self):
        assert CommunityBot._is_community_command(_bot(aliases=["!cb"]), "cb")

    def test_empty_list(self):
        assert not CommunityBot._is_community_command(_bot(aliases=[]), "cb")

    def test_none_list(self):
        assert not CommunityBot._is_community_command(_bot(aliases=None), "cb")
        bot = _bot()
        bot.config["command_aliases"] = None
        assert not CommunityBot._is_community_command(bot, "cb")

    @pytest.mark.parametrize("alias", ["", "!"])
    def test_empty_alias_does_not_match_empty(self, alias):
        assert not CommunityBot._is_community_command(_bot(aliases=[alias]), "")

    def test_missing_key(self):
        assert not CommunityBot._is_community_command(_bot(), "cb")


# --- _get_membership ---------------------------------------------------------


class TestGetMembership:
    @pytest.mark.asyncio
    async def test_returns_membership(self):
        bot = _bot(memberships={(PARENT, USER): Membership.JOIN})
        assert await CommunityBot._get_membership(bot, PARENT, USER) == Membership.JOIN

    @pytest.mark.asyncio
    async def test_not_found_is_none(self):
        bot = _bot()
        assert await CommunityBot._get_membership(bot, PARENT, USER) is None

    @pytest.mark.asyncio
    async def test_other_exception_is_none_and_logged(self):
        bot = _bot()
        bot.client.get_state_event = AsyncMock(side_effect=RuntimeError("boom"))
        assert await CommunityBot._get_membership(bot, PARENT, USER) is None
        bot.log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_membership_attr_is_none(self):
        bot = _bot()
        bot.client.get_state_event = AsyncMock(return_value=object())
        assert await CommunityBot._get_membership(bot, PARENT, USER) is None


# --- _invite_to_room ---------------------------------------------------------


class TestInviteToRoom:
    def _bound(self, bot):
        bot._get_membership = lambda r, u: CommunityBot._get_membership(bot, r, u)
        return bot

    @pytest.mark.asyncio
    async def test_joined(self):
        bot = _bot(memberships={("!r:x", USER): Membership.JOIN})
        assert await CommunityBot._invite_to_room(bot, "!r:x", USER) == "joined"
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_invited(self):
        bot = _bot(memberships={("!r:x", USER): Membership.INVITE})
        assert await CommunityBot._invite_to_room(bot, "!r:x", USER) == "already invited"
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [None, Membership.LEAVE, Membership.BAN])
    async def test_invited(self, state):
        mem = {("!r:x", USER): state} if state else {}
        bot = _bot(memberships=mem)
        assert await CommunityBot._invite_to_room(bot, "!r:x", USER) == "invited"
        bot.client.invite_user.assert_awaited_once_with("!r:x", USER, reason=None)

    @pytest.mark.asyncio
    async def test_invite_error_propagates(self):
        bot = _bot()
        bot.client.invite_user = AsyncMock(side_effect=RuntimeError("no power"))
        with pytest.raises(RuntimeError):
            await CommunityBot._invite_to_room(bot, "!r:x", USER)


# --- user invite -------------------------------------------------------------


def _wire(bot):
    bot._invite_to_room = lambda *a, **kw: CommunityBot._invite_to_room(bot, *a, **kw)
    bot._could_self_join = lambda r, u: CommunityBot._could_self_join(bot, r, u)
    bot._get_membership = lambda r, u: CommunityBot._get_membership(bot, r, u)
    bot._summarize_rooms = CommunityBot._summarize_rooms
    return bot


async def _user_invite(bot, evt, mxid):
    fn = CommunityBot.user_invite.__mb_func__.__wrapped__.__wrapped__
    await fn(bot, evt, mxid)


async def _room_invite(bot, evt, mxid):
    fn = CommunityBot.room_invite.__mb_func__.__wrapped__
    await fn(bot, evt, mxid)


class TestUserInvite:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        bot = _wire(_bot(invite_rooms=["!a:x", "!b:x"]))
        evt = _evt()
        await _user_invite(bot, evt, TARGET)
        invited = [c.args[0] for c in bot.client.invite_user.await_args_list]
        assert invited == [PARENT, "!a:x", "!b:x"]
        assert all(
            c.kwargs["reason"] == f"invited by {MOD}"
            for c in bot.client.invite_user.await_args_list
        )
        bot._post_notification.assert_awaited_once()
        assert "Invited" in _reply_text(evt)
        bot._post_notification.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_banned_refused(self):
        bot = _wire(_bot())
        bot.check_if_banned = AsyncMock(return_value=True)
        evt = _evt()
        await _user_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_not_awaited()
        assert "banlist" in _reply_text(evt)
        bot._post_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_mxid(self):
        bot = _wire(_bot())
        evt = _evt()
        await _user_invite(bot, evt, "notanmxid")
        bot.client.invite_user.assert_not_awaited()
        bot.check_if_banned.assert_not_awaited()
        assert "not a valid matrix ID" in _reply_text(evt)

    @pytest.mark.asyncio
    async def test_parent_dedup(self):
        bot = _wire(_bot(invite_rooms=[PARENT, "!a:x", "!a:x"]))
        await _user_invite(bot, _evt(), TARGET)
        invited = [c.args[0] for c in bot.client.invite_user.await_args_list]
        assert invited == [PARENT, "!a:x"]

    @pytest.mark.asyncio
    async def test_non_room_id_entry_is_error(self):
        bot = _wire(_bot(invite_rooms=["#alias:x"]))
        evt = _evt()
        await _user_invite(bot, evt, TARGET)
        invited = [c.args[0] for c in bot.client.invite_user.await_args_list]
        assert invited == [PARENT]
        text = _reply_text(evt)
        assert "Failed" in text and "#alias:x" in text

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        bot = _wire(_bot(invite_rooms=["!bad:x"]))

        async def invite(room_id, user, **kw):
            if room_id == "!bad:x":
                raise RuntimeError("forbidden")

        bot.client.invite_user = AsyncMock(side_effect=invite)
        evt = _evt()
        await _user_invite(bot, evt, TARGET)
        text = _reply_text(evt)
        assert "Failed" in text and "forbidden" in text
        bot._post_notification.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_notification_when_nothing_invited(self):
        bot = _wire(
            _bot(memberships={(PARENT, TARGET): Membership.JOIN}, invite_rooms=[])
        )
        evt = _evt()
        await _user_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_not_awaited()
        assert "Skipped" in _reply_text(evt)
        bot._post_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_notification_when_all_fail(self):
        bot = _wire(_bot())
        bot.client.invite_user = AsyncMock(side_effect=RuntimeError("x"))
        await _user_invite(bot, _evt(), TARGET)
        bot._post_notification.assert_not_awaited()


# --- room invite -------------------------------------------------------------


class TestRoomInvite:
    @pytest.mark.asyncio
    async def test_room_outside_community(self):
        bot = _wire(_bot())
        evt = _evt(room_id="!other:x")
        await _room_invite(bot, evt, TARGET)
        assert "isn't part of the community" in _reply_text(evt)
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parent_room_allowed(self):
        bot = _wire(_bot(mod=True))
        evt = _evt(room_id=PARENT)
        await _room_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_awaited_once_with(
            PARENT, TARGET, reason=f"invited by {MOD}"
        )

    @pytest.mark.asyncio
    async def test_invalid_mxid(self):
        bot = _wire(_bot())
        evt = _evt()
        await _room_invite(bot, evt, "bad")
        assert "not a valid matrix ID" in _reply_text(evt)
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_member_sender_refused(self):
        bot = _wire(
            _bot(mod=False, memberships={(PARENT, TARGET): Membership.JOIN})
        )
        evt = _evt(sender=USER)
        await _room_invite(bot, evt, TARGET)
        assert "Only community members" in _reply_text(evt)
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_member_target_refused_for_non_mod(self):
        bot = _wire(_bot(mod=False, memberships={(PARENT, USER): Membership.JOIN}))
        evt = _evt(sender=USER)
        await _room_invite(bot, evt, TARGET)
        assert "isn't a member of the community" in _reply_text(evt)
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_member_invites_member(self):
        bot = _wire(
            _bot(
                mod=False,
                memberships={
                    (PARENT, USER): Membership.JOIN,
                    (PARENT, TARGET): Membership.JOIN,
                },
            )
        )
        evt = _evt(sender=USER)
        await _room_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_awaited_once_with(
            "!child:example.com", TARGET, reason=f"invited by {evt.sender}"
        )
        assert _reply_text(evt) == f"Invited {TARGET}."

    @pytest.mark.asyncio
    async def test_mod_can_invite_non_member(self):
        bot = _wire(_bot(mod=True))
        evt = _evt()
        await _room_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_awaited_once_with(
            "!child:example.com", TARGET, reason=f"invited by {evt.sender}"
        )
        assert _reply_text(evt) == f"Invited {TARGET}."

    @pytest.mark.asyncio
    async def test_already_joined_reply(self):
        bot = _wire(
            _bot(mod=True, memberships={("!child:example.com", TARGET): Membership.JOIN})
        )
        evt = _evt()
        await _room_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_not_awaited()
        assert _reply_text(evt) == f"{TARGET} is joined."

    @pytest.mark.asyncio
    async def test_invite_error_reply(self):
        bot = _wire(_bot(mod=True))
        bot.client.invite_user = AsyncMock(side_effect=RuntimeError("banned there"))
        evt = _evt()
        await _room_invite(bot, evt, TARGET)
        assert "Couldn't invite" in _reply_text(evt)
        assert "banned there" in _reply_text(evt)

    @pytest.mark.asyncio
    async def test_mark_read(self):
        bot = _wire(_bot())
        evt = _evt()
        await _room_invite(bot, evt, TARGET)
        evt.mark_read.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_mod_refused_in_invite_only_room(self):
        bot = _wire(
            _bot(
                mod=False,
                memberships={
                    (PARENT, USER): Membership.JOIN,
                    (PARENT, TARGET): Membership.JOIN,
                },
                join_rules={"!child:example.com": _jr(JoinRule.INVITE)},
            )
        )
        evt = _evt(sender=USER)
        await _room_invite(bot, evt, TARGET)
        assert "only a moderator can invite" in _reply_text(evt)
        bot.client.invite_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mod_allowed_in_invite_only_room(self):
        bot = _wire(
            _bot(
                mod=True,
                join_rules={"!child:example.com": _jr(JoinRule.INVITE)},
            )
        )
        evt = _evt()
        await _room_invite(bot, evt, TARGET)
        bot.client.invite_user.assert_awaited_once()
        assert _reply_text(evt) == f"Invited {TARGET}."

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mod", [True, False])
    async def test_banned_target_refused(self, mod):
        bot = _wire(
            _bot(
                mod=mod,
                memberships={
                    (PARENT, USER): Membership.JOIN,
                    (PARENT, TARGET): Membership.JOIN,
                },
            )
        )
        bot.check_if_banned = AsyncMock(return_value=True)
        evt = _evt(sender=MOD if mod else USER)
        await _room_invite(bot, evt, TARGET)
        assert "banlist" in _reply_text(evt)
        bot.client.invite_user.assert_not_awaited()


class TestCouldSelfJoin:
    ROOM = "!r:x"

    async def _call(self, jr, memberships=None):
        bot = _wire(_bot(memberships=memberships, join_rules={self.ROOM: jr}))
        return await CommunityBot._could_self_join(bot, self.ROOM, USER)

    @pytest.mark.asyncio
    async def test_public(self):
        assert await self._call(_jr(JoinRule.PUBLIC)) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rule", [JoinRule.RESTRICTED, JoinRule.KNOCK_RESTRICTED])
    async def test_restricted_member_of_allowed_room(self, rule):
        jr = _jr(rule, ["!other:x", "!allowed:x"])
        mem = {("!allowed:x", USER): Membership.JOIN}
        assert await self._call(jr, mem) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rule", [JoinRule.RESTRICTED, JoinRule.KNOCK_RESTRICTED])
    async def test_restricted_not_member(self, rule):
        jr = _jr(rule, ["!allowed:x"])
        mem = {("!allowed:x", USER): Membership.INVITE}
        assert await self._call(jr, mem) is False

    @pytest.mark.asyncio
    async def test_restricted_no_allow(self):
        assert await self._call(_jr(JoinRule.RESTRICTED)) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rule", [JoinRule.INVITE, JoinRule.KNOCK])
    async def test_invite_knock_private(self, rule):
        assert await self._call(_jr(rule)) is False

    @pytest.mark.asyncio
    async def test_missing_rule(self):
        assert await self._call(Mock(spec=[])) is False

    @pytest.mark.asyncio
    async def test_lookup_error_fails_closed(self):
        assert await self._call(MNotFound("nf", 404)) is False
        assert await self._call(RuntimeError("boom")) is False
