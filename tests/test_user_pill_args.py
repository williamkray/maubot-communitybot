"""Tests for user mention-pill arguments (helpers and command wiring)."""

import pytest
from unittest.mock import Mock, AsyncMock

from community.bot import CommunityBot
from community.helpers import common_utils as cu
from community.helpers.common_utils import (
    _user_id_from_href,
    extract_user_pills,
    split_user_arg,
)

ALICE = "@alice:example.org"


def pill(text, user, encoded=True):
    ref = user.replace("@", "%40").replace(":", "%3A") if encoded else user
    return f'<a href="https://matrix.to/#/{ref}">{text}</a>'


# ---------------------------------------------------------------- helpers
class TestUserIdFromHref:
    def test_percent_encoded(self):
        assert _user_id_from_href("https://matrix.to/#/%40alice%3Aexample.org") == ALICE

    def test_unencoded(self):
        assert _user_id_from_href("https://matrix.to/#/@alice:example.org") == ALICE

    def test_matrix_uri(self):
        assert _user_id_from_href("matrix:u/alice:example.org?action=chat") == ALICE

    def test_matrix_uri_no_query(self):
        assert _user_id_from_href("matrix:u/alice:example.org") == ALICE

    def test_matrix_to_with_query(self):
        assert (
            _user_id_from_href("https://matrix.to/#/@alice:example.org?via=a.org")
            == ALICE
        )

    def test_html_entities_unescaped(self):
        href = "https://matrix.to/#/%40alice%3Aexample.org?via=a&amp;via=b"
        assert _user_id_from_href(href) == ALICE

    def test_room_alias_link(self):
        assert _user_id_from_href("https://matrix.to/#/%23room%3Aexample.org") is None

    def test_room_id_link(self):
        assert _user_id_from_href("https://matrix.to/#/!abc:example.org") is None

    def test_matrix_uri_room(self):
        assert _user_id_from_href("matrix:r/room:example.org") is None

    def test_junk(self):
        assert _user_id_from_href("https://example.com/foo") is None
        assert _user_id_from_href("") is None


class TestExtractUserPills:
    def test_basic(self):
        body = f"hi {pill('William Kray', ALICE)}"
        assert extract_user_pills(body) == [("William Kray", ALICE)]

    def test_skips_room_links(self):
        body = '<a href="https://matrix.to/#/%23r%3Ax.org">#r:x.org</a> ' + pill("A", ALICE)
        assert extract_user_pills(body) == [("A", ALICE)]

    def test_entity_text_unescaped(self):
        assert extract_user_pills(pill("Tom &amp; Jerry", ALICE)) == [
            ("Tom & Jerry", ALICE)
        ]

    @pytest.mark.parametrize("body", [None, ""])
    def test_empty(self, body):
        assert extract_user_pills(body) == []


class TestSplitUserArg:
    def test_typed_id(self):
        assert split_user_arg("@bob:x.org #room:x.org", None) == ("@bob:x.org", "#room:x.org")

    def test_typed_id_only(self):
        assert split_user_arg("@bob:x.org", None) == ("@bob:x.org", "")

    def test_pill_wins_over_lookalike_display_name(self):
        # Someone named "@bob:x.org" is mentioned: act on the link's target.
        body = pill("@bob:x.org", ALICE)
        assert split_user_arg("@bob:x.org rest", body) == (ALICE, "rest")

    def test_typed_id_used_when_no_pill_matches(self):
        body = pill("Somebody Else", ALICE)
        assert split_user_arg("@bob:x.org rest", body) == ("@bob:x.org", "rest")

    def test_typed_id_must_be_well_formed(self):
        assert split_user_arg("@Will: the admin", None) == (None, "@Will: the admin")
        assert split_user_arg("@a:b\n!room:x", None) == ("@a:b", "!room:x")

    def test_same_text_two_users_is_refused(self):
        body = pill("Al", ALICE) + " " + pill("Al", "@other:x.org")
        assert split_user_arg("Al", body) == (None, "Al")

    def test_same_text_same_user_twice_is_fine(self):
        body = pill("Al", ALICE) + " " + pill("Al", ALICE)
        assert split_user_arg("Al", body) == (ALICE, "")

    def test_leading_at_before_display_name(self):
        assert split_user_arg("@William Kray", pill("William Kray", ALICE)) == (ALICE, "")

    def test_matrix_to_host_must_match(self):
        from community.helpers.common_utils import _user_id_from_href

        assert _user_id_from_href("https://evil.example/?q=matrix.to/#/@mod:x.org") is None
        assert _user_id_from_href("https://matrix.to.evil.example/#/@mod:x.org") is None
        assert _user_id_from_href("https://MATRIX.TO/#/%40mod%3Ax.org") == "@mod:x.org"

    def test_multi_word_name(self):
        body = pill("William Kray", ALICE)
        assert split_user_arg("William Kray #room:x.org", body) == (ALICE, "#room:x.org")

    def test_name_only(self):
        assert split_user_arg("William Kray", pill("William Kray", ALICE)) == (ALICE, "")

    def test_word_boundary(self):
        body = pill("William", ALICE)
        assert split_user_arg("Williams", body) == (None, "Williams")

    def test_prefix_longest_wins(self):
        bob = "@bob:example.org"
        body = pill("Will", ALICE) + " " + pill("Will Smith", bob)
        assert split_user_arg("Will Smith foo", body) == (bob, "foo")
        assert split_user_arg("Will foo", body) == (ALICE, "foo")

    def test_prefix_longest_wins_reverse_order(self):
        bob = "@bob:example.org"
        body = pill("Will Smith", bob) + " " + pill("Will", ALICE)
        assert split_user_arg("Will Smith", body) == (bob, "")

    def test_pill_text_is_user_id(self):
        body = pill(ALICE, ALICE)
        assert split_user_arg(f"{ALICE} x", body) == (ALICE, "x")

    def test_pill_text_is_user_id_without_typed_form(self):
        # text lacking ":" so typed shortcut does not apply
        body = pill("@alice", ALICE)
        assert split_user_arg("@alice sub", body) == (ALICE, "sub")

    def test_entity_escaped_text(self):
        body = pill("Tom &amp; Jerry", ALICE)
        assert split_user_arg("Tom & Jerry #r:x.org", body) == (ALICE, "#r:x.org")

    def test_no_match(self):
        assert split_user_arg("Bob Jones", pill("William", ALICE)) == (None, "Bob Jones")

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_raw(self, raw):
        assert split_user_arg(raw, pill("W", ALICE)) == (None, "")

    @pytest.mark.parametrize("body", [None, ""])
    def test_empty_body(self, body):
        assert split_user_arg("William Kray", body) == (None, "William Kray")

    def test_match_must_be_at_start(self):
        assert split_user_arg("x William", pill("William", ALICE)) == (None, "x William")


# ------------------------------------------------------------ command wiring
def _bot(levels=None):
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {"parent_room": "!parent:x.org", "invite_rooms": []}
    bot.log = Mock()
    bot.client = Mock()
    bot.check_parent_room = AsyncMock(return_value=True)
    levels = levels if levels is not None else {"@mod:x.org": 100}
    bot.user_permitted = AsyncMock(
        side_effect=lambda u, lvl, room_id=None: levels.get(u, 0) >= lvl
    )
    bot.is_user_in_parent_space = AsyncMock(return_value=False)
    bot.ban_this_user = AsyncMock(
        return_value={"ban_list": [], "error_list": []}
    )
    bot.do_sync = AsyncMock()
    bot._format_results = Mock(return_value="done")
    bot.check_if_banned = AsyncMock(return_value=False)
    bot._parent_invite_level = AsyncMock(return_value=50)
    return bot


def _evt(body=None, sender="@mod:x.org"):
    evt = Mock()
    evt.sender = sender
    evt.room_id = "!parent:x.org"
    evt.reply = AsyncMock()
    evt.respond = AsyncMock()
    evt.mark_read = AsyncMock()
    evt.content = Mock()
    evt.content.formatted_body = body
    return evt


def _fn(name):
    return getattr(CommunityBot, name).__mb_func__


class TestBan:
    @pytest.mark.asyncio
    async def test_pill_resolves(self):
        bot = _bot()
        evt = _evt(pill("William Kray", ALICE))
        await _fn("user_ban")(bot, evt, "William Kray")
        bot.ban_this_user.assert_awaited_once_with(ALICE, all_rooms=True)

    @pytest.mark.asyncio
    async def test_typed_id(self):
        bot = _bot()
        await _fn("user_ban")(bot, _evt(), "@bob:x.org")
        bot.ban_this_user.assert_awaited_once_with("@bob:x.org", all_rooms=True)

    @pytest.mark.asyncio
    async def test_no_matching_pill(self):
        bot = _bot()
        evt = _evt(pill("Someone Else", ALICE))
        await _fn("user_ban")(bot, evt, "William Kray")
        bot.ban_this_user.assert_not_awaited()
        assert "Please give one user" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_extra_text_rejected(self):
        bot = _bot()
        evt = _evt(pill("William Kray", ALICE))
        await _fn("user_ban")(bot, evt, "William Kray extra")
        bot.ban_this_user.assert_not_awaited()
        assert "Please give one user" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_non_str_formatted_body_ignored(self):
        bot = _bot()
        evt = _evt(Mock())  # Mock formatted_body, not a str
        await _fn("user_ban")(bot, evt, "William Kray")
        bot.ban_this_user.assert_not_awaited()
        assert "Please give one user" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_permission_checked_before_user_arg(self):
        bot = _bot()
        evt = _evt(None, sender="@rando:x.org")
        await _fn("user_ban")(bot, evt, "nonsense")
        assert evt.reply.await_args.args[0] == "You don't have permission to use this command"
        bot.ban_this_user.assert_not_awaited()


class TestBancheckInvitePermissionOrder:
    @pytest.mark.asyncio
    async def test_bancheck_non_member_gets_permission_reply(self):
        bot = _bot()
        evt = _evt(None, sender="@rando:x.org")
        await _fn("user_bancheck")(bot, evt, "nonsense")
        evt.reply.assert_awaited_once_with("Only community members can use this command")
        bot.check_if_banned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bancheck_member_pill(self):
        bot = _bot()
        evt = _evt(pill("William Kray", ALICE))
        await _fn("user_bancheck")(bot, evt, "William Kray")
        bot.check_if_banned.assert_awaited_once_with(ALICE)

    @pytest.mark.asyncio
    async def test_bancheck_member_bad_arg(self):
        bot = _bot()
        evt = _evt(None)
        await _fn("user_bancheck")(bot, evt, "nobody")
        bot.check_if_banned.assert_not_awaited()
        assert "Please give one user" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_invite_unpermitted_gets_permission_reply(self):
        bot = _bot()
        evt = _evt(None, sender="@rando:x.org")
        await _fn("user_invite")(bot, evt, "nonsense")
        assert "permission to invite" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_invite_permitted_bad_arg(self):
        bot = _bot()
        evt = _evt(None)
        await _fn("user_invite")(bot, evt, "nonsense")
        assert "Please give one user" in evt.reply.await_args.args[0]
        evt.mark_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invite_pill_reaches_ban_check(self):
        bot = _bot()
        bot.check_if_banned = AsyncMock(return_value=True)
        evt = _evt(pill("William Kray", ALICE))
        await _fn("user_invite")(bot, evt, "William Kray")
        bot.check_if_banned.assert_awaited_once_with(ALICE)


class TestRedact:
    @pytest.mark.asyncio
    async def test_user_and_room(self):
        bot = _bot()
        bot._resolve_room_arg = AsyncMock(return_value="!room:x.org")
        bot.get_messages_to_redact = AsyncMock(return_value=[])
        evt = _evt(pill("William Kray", ALICE))
        await _fn("user_redact")(bot, evt, "William Kray #room:server")
        bot._resolve_room_arg.assert_awaited_once_with("#room:server", evt)
        bot.get_messages_to_redact.assert_awaited_once_with("!room:x.org", ALICE)

    @pytest.mark.asyncio
    async def test_user_only_room_empty(self):
        bot = _bot()
        bot._resolve_room_arg = AsyncMock(return_value="!room:x.org")
        bot.get_messages_to_redact = AsyncMock(return_value=[])
        evt = _evt(pill("William Kray", ALICE))
        await _fn("user_redact")(bot, evt, "William Kray")
        bot._resolve_room_arg.assert_awaited_once_with("", evt)

    @pytest.mark.asyncio
    async def test_typed(self):
        bot = _bot()
        bot._resolve_room_arg = AsyncMock(return_value="!room:x.org")
        bot.get_messages_to_redact = AsyncMock(return_value=[])
        evt = _evt()
        await _fn("user_redact")(bot, evt, "@bob:x.org #r:x.org")
        bot._resolve_room_arg.assert_awaited_once_with("#r:x.org", evt)
        bot.get_messages_to_redact.assert_awaited_once_with("!room:x.org", "@bob:x.org")

    @pytest.mark.asyncio
    async def test_no_user(self):
        bot = _bot()
        bot._resolve_room_arg = AsyncMock()
        evt = _evt()
        await _fn("user_redact")(bot, evt, "William Kray #room:server")
        bot._resolve_room_arg.assert_not_awaited()
        assert "Please give a user" in evt.reply.await_args.args[0]


def _delegation_bot():
    bot = _bot()
    bot._resolve_delegation_target = AsyncMock(return_value="!sub:x.org")
    bot.get_space_roomlist = AsyncMock(return_value=["!r1:x.org"])
    bot._apply_user_levels = AsyncMock(return_value=(["!sub:x.org"], []))
    bot._remove_user_from_rooms = AsyncMock(return_value=(["!sub:x.org"], []))
    bot._get_room_name = AsyncMock(return_value="Sub")
    bot.client.get_state_event = AsyncMock(return_value={"users": {}})
    return bot


class TestDelegate:
    @pytest.mark.asyncio
    async def test_pill(self):
        bot = _delegation_bot()
        evt = _evt(pill("Alice Smith", ALICE))
        await _fn("space_delegate")(bot, evt, "Alice Smith #sub:server mod")
        bot._resolve_delegation_target.assert_awaited_once_with("#sub:server", evt)
        bot._apply_user_levels.assert_awaited_once_with(
            ["!sub:x.org", "!r1:x.org"], {ALICE: 50}, mode="max"
        )

    @pytest.mark.asyncio
    async def test_pill_default_level(self):
        bot = _delegation_bot()
        evt = _evt(pill("Alice Smith", ALICE))
        await _fn("space_delegate")(bot, evt, "Alice Smith #sub:server")
        bot._resolve_delegation_target.assert_awaited_once_with("#sub:server", evt)
        assert bot._apply_user_levels.await_args.args[1] == {ALICE: 100}

    @pytest.mark.asyncio
    async def test_typed_form(self):
        bot = _delegation_bot()
        evt = _evt()
        await _fn("space_delegate")(bot, evt, "@alice:server #sub:server")
        bot._resolve_delegation_target.assert_awaited_once_with("#sub:server", evt)
        assert list(bot._apply_user_levels.await_args.args[1]) == ["@alice:server"]

    @pytest.mark.asyncio
    async def test_typed_numeric_level(self):
        bot = _delegation_bot()
        await _fn("space_delegate")(bot, _evt(), "@alice:server #sub:server 75")
        assert bot._apply_user_levels.await_args.args[1] == {"@alice:server": 75}

    @pytest.mark.asyncio
    async def test_unresolved_name_rejected(self):
        bot = _delegation_bot()
        evt = _evt()
        await _fn("space_delegate")(bot, evt, "Alice #sub:server")
        bot._apply_user_levels.assert_not_awaited()
        assert "full matrix id" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_missing_subspace_usage(self):
        bot = _delegation_bot()
        evt = _evt(pill("Alice Smith", ALICE))
        await _fn("space_delegate")(bot, evt, "Alice Smith")
        bot._apply_user_levels.assert_not_awaited()
        assert "usage" in evt.reply.await_args.args[0]

    @pytest.mark.asyncio
    async def test_requires_admin(self):
        bot = _delegation_bot()
        bot.user_permitted = AsyncMock(return_value=False)
        evt = _evt(pill("Alice Smith", ALICE))
        await _fn("space_delegate")(bot, evt, "Alice Smith #sub:server")
        bot._apply_user_levels.assert_not_awaited()


class TestUndelegate:
    @pytest.mark.asyncio
    async def test_pill(self):
        bot = _delegation_bot()
        evt = _evt(pill("Alice Smith", ALICE))
        await _fn("space_undelegate")(bot, evt, "Alice Smith #sub:server")
        bot._resolve_delegation_target.assert_awaited_once_with("#sub:server", evt)
        bot._remove_user_from_rooms.assert_awaited_once_with(
            ["!sub:x.org", "!r1:x.org"], ALICE, keep_level=None
        )

    @pytest.mark.asyncio
    async def test_typed(self):
        bot = _delegation_bot()
        evt = _evt()
        await _fn("space_undelegate")(bot, evt, "@alice:server #sub:server")
        assert bot._remove_user_from_rooms.await_args.args[1] == "@alice:server"

    @pytest.mark.asyncio
    async def test_no_user_usage(self):
        bot = _delegation_bot()
        evt = _evt()
        await _fn("space_undelegate")(bot, evt, "Alice #sub:server")
        bot._remove_user_from_rooms.assert_not_awaited()
        assert "usage" in evt.reply.await_args.args[0]
