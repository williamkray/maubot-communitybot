"""Tests for proactive banning from subscribed banlists.

The bot is created with __new__ to bypass the heavy constructor (mirrors
tests/test_space_roomlist.py), and only the attributes the methods touch are
set. No live Matrix calls are made.
"""

import asyncio

import pytest
from unittest.mock import Mock, AsyncMock, patch

from mautrix.types import Obj

from community.bot import CommunityBot


BANLIST_ID = "!bl:example.com"
BANLIST_ALIAS = "#bl:example.com"


def make_bot():
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {
        "proactive_banning": True,
        "banlists": [BANLIST_ALIAS],
        "parent_room": "!parent:example.com",
        "redact_on_ban": False,
        "sleep": 0,
    }
    bot.log = Mock()
    bot._banlist_cache = None
    bot._proactive_ban_semaphore = None
    bot.client = Mock()
    bot.client.resolve_room_alias = AsyncMock(return_value={"room_id": BANLIST_ID})
    return bot


def policy_event(entity="@spammer:example.com", recommendation="m.ban", reason="spam"):
    evt = Mock()
    evt.room_id = BANLIST_ID
    content = {"entity": entity, "recommendation": recommendation}
    if reason is not None:
        content["reason"] = reason
    evt.content = Obj(**content)
    return evt


class TestCheckBanEvent:

    @pytest.mark.asyncio
    async def test_passes_members_only_and_source(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()

        await bot.check_ban_event(policy_event())

        bot.ban_this_user.assert_awaited_once()
        args, kwargs = bot.ban_this_user.call_args
        assert args == ("@spammer:example.com",)
        assert kwargs["members_only"] is True
        assert kwargs["quiet_if_empty"] is True
        assert kwargs["source"] == f"{BANLIST_ALIAS} (spam)"
        assert kwargs["reason"] == f"banlist {BANLIST_ALIAS} (spam)"

    @pytest.mark.asyncio
    async def test_source_without_policy_reason(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()

        await bot.check_ban_event(policy_event(reason=None))

        assert bot.ban_this_user.call_args.kwargs["source"] == BANLIST_ALIAS

    @pytest.mark.asyncio
    async def test_empty_rule_ignored(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()
        evt = Mock()
        evt.room_id = BANLIST_ID
        evt.content = Obj()

        await bot.check_ban_event(evt)

        bot.ban_this_user.assert_not_called()
        bot.log.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_banlist_alias_resolved_once(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()

        for _ in range(5):
            await bot.check_ban_event(policy_event())

        bot.client.resolve_room_alias.assert_awaited_once_with(BANLIST_ALIAS)

    @pytest.mark.asyncio
    async def test_concurrent_events_share_one_resolution(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()

        await asyncio.gather(*(bot.check_ban_event(policy_event()) for _ in range(20)))

        bot.client.resolve_room_alias.assert_awaited_once_with(BANLIST_ALIAS)

    @pytest.mark.asyncio
    async def test_failed_resolution_retried_after_backoff(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()
        bot.client.resolve_room_alias = AsyncMock(side_effect=Exception("timeout"))

        await bot.check_ban_event(policy_event())
        await bot.check_ban_event(policy_event())
        # Within the retry window the failure is reused, not re-resolved
        assert bot.client.resolve_room_alias.await_count == 1
        bot.ban_this_user.assert_not_called()

        bot._banlist_cache_expires = 0  # window elapsed
        bot.client.resolve_room_alias = AsyncMock(return_value={"room_id": BANLIST_ID})
        await bot.check_ban_event(policy_event())

        bot.client.resolve_room_alias.assert_awaited_once()
        bot.ban_this_user.assert_awaited_once()
        assert bot._banlist_cache_expires is None

    @pytest.mark.asyncio
    async def test_alias_and_id_for_same_room_still_cached(self):
        bot = make_bot()
        bot.config["banlists"] = [BANLIST_ALIAS, BANLIST_ID]
        bot.ban_this_user = AsyncMock()

        await bot.check_ban_event(policy_event())
        await bot.check_ban_event(policy_event())

        bot.client.resolve_room_alias.assert_awaited_once()
        assert bot._banlist_cache_expires is None

    @pytest.mark.asyncio
    async def test_long_policy_reason_truncated(self):
        bot = make_bot()
        bot.ban_this_user = AsyncMock()

        await bot.check_ban_event(policy_event(reason="x" * 1000))

        source = bot.ban_this_user.call_args.kwargs["source"]
        assert len(source) < 250
        assert source.endswith("…)")

    @pytest.mark.asyncio
    async def test_check_if_banned_uses_resolved_ids(self):
        bot = make_bot()
        with patch(
            "community.bot.user_utils.check_if_banned", AsyncMock(return_value=True)
        ) as check:
            await bot.check_if_banned("@u:example.com")
            await bot.check_if_banned("@u:example.com")

        assert check.call_args.args[2] == [BANLIST_ID]
        bot.client.resolve_room_alias.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_bans_are_bounded(self):
        bot = make_bot()
        active = 0
        peak = 0

        async def slow_ban(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

        bot.ban_this_user = slow_ban

        await asyncio.gather(
            *(bot.check_ban_event(policy_event(entity=f"@u{i}:example.com"))
              for i in range(20))
        )

        assert peak == CommunityBot._PROACTIVE_BAN_CONCURRENCY


class TestBanThisUserNotification:

    def _bot(self):
        bot = make_bot()
        bot.get_space_roomlist = AsyncMock(return_value=["!a:example.com"])
        bot._post_notification = AsyncMock()
        bot.database = Mock()
        return bot

    async def _run(self, bot, results, **kwargs):
        with patch(
            "community.bot.user_utils.ban_user_from_rooms",
            AsyncMock(return_value=results),
        ):
            return await bot.ban_this_user("@spammer:example.com", **kwargs)

    @pytest.mark.asyncio
    async def test_quiet_when_nothing_happened(self):
        bot = self._bot()

        await self._run(
            bot,
            {"ban_list": {"@spammer:example.com": []}, "error_list": {}},
            quiet_if_empty=True, members_only=True, source=BANLIST_ALIAS,
        )

        bot._post_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_notice_names_source_and_rooms(self):
        bot = self._bot()

        await self._run(
            bot,
            {"ban_list": {"@spammer:example.com": ["Waiting Room"]}, "error_list": {}},
            quiet_if_empty=True, members_only=True, source=f"{BANLIST_ALIAS} (spam)",
        )

        readout = bot._post_notification.call_args.args[0]
        assert "1 room(s): Waiting Room" in readout
        assert f"Source: {BANLIST_ALIAS} (spam)." in readout
        assert "⚠️" not in readout

    @pytest.mark.asyncio
    async def test_notice_counts_failed_rooms(self):
        bot = self._bot()

        await self._run(
            bot,
            {
                "ban_list": {"@spammer:example.com": []},
                "error_list": {"@spammer:example.com": ["Room A", "Room B"]},
            },
            quiet_if_empty=True, members_only=True,
        )

        readout = bot._post_notification.call_args.args[0]
        assert "Ban failed in 2 room(s): Room A, Room B." in readout

    @pytest.mark.asyncio
    async def test_source_is_html_escaped(self):
        bot = self._bot()

        await self._run(
            bot,
            {"ban_list": {"@spammer:example.com": ["Room"]}, "error_list": {}},
            quiet_if_empty=True, members_only=True,
            source='#bl (<a href="https://evil">click</a>)',
        )

        readout = bot._post_notification.call_args.args[0]
        assert "<a href" not in readout
        assert "&lt;a href=" in readout
