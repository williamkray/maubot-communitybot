"""Tests for user utility functions."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from mautrix.types import EventType, Membership, UserID
from mautrix.errors import MNotFound

from community.helpers.user_utils import (
    check_if_banned, get_banlist_roomids, ban_user_from_rooms, user_permitted,
    resolve_banlists,
)


class TestUserUtils:
    """Test cases for user utility functions."""

    @pytest.mark.asyncio
    async def test_check_if_banned_success(self):
        """Test successful ban check."""
        client = Mock()
        client.get_joined_rooms = AsyncMock(return_value=["!room1:example.com", "!room2:example.com"])
        
        # Mock state events
        ban_rule = Mock()
        ban_rule.type.t = "m.policy.rule.user"
        ban_rule.content = {
            "entity": "@banned:example.com",
            "recommendation": "ban"
        }
        
        client.get_state = AsyncMock(return_value=[ban_rule])
        
        # Mock get_banlist_roomids to return the room ID directly
        with patch('community.helpers.user_utils.get_banlist_roomids') as mock_get_banlists:
            mock_get_banlists.return_value = ["!room1:example.com"]
            
            logger = Mock()
            result = await check_if_banned(client, "@banned:example.com", ["!room1:example.com"], logger)
            
            assert result == True

    @pytest.mark.asyncio
    async def test_check_if_banned_not_banned(self):
        """Test ban check when user is not banned."""
        client = Mock()
        client.get_joined_rooms = AsyncMock(return_value=["!room1:example.com"])
        
        with patch('community.helpers.user_utils.get_banlist_roomids') as mock_get_banlists:
            mock_get_banlists.return_value = ["!room1:example.com"]
            
            # Mock state events with no ban rules
            client.get_state = AsyncMock(return_value=[])
            
            logger = Mock()
            result = await check_if_banned(client, "@user:example.com", ["!room1:example.com"], logger)
            
            assert result == False

    @pytest.mark.asyncio
    async def test_check_if_banned_room_not_joined(self):
        """Test ban check when bot is not in banlist room."""
        client = Mock()
        client.get_joined_rooms = AsyncMock(return_value=["!room2:example.com"])
        
        with patch('community.helpers.user_utils.get_banlist_roomids') as mock_get_banlists:
            mock_get_banlists.return_value = ["!room1:example.com"]
            
            logger = Mock()
            result = await check_if_banned(client, "@user:example.com", ["!room1:example.com"], logger)
            
            assert result == False
            logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_get_banlist_roomids_aliases(self):
        """Test getting banlist room IDs with aliases."""
        client = Mock()
        client.resolve_room_alias = AsyncMock(return_value={"room_id": "!room1:example.com"})
        
        banlists = ["#banlist1:example.com", "!room2:example.com"]
        logger = Mock()
        
        result = await get_banlist_roomids(client, banlists, logger)
        
        assert "!room1:example.com" in result
        assert "!room2:example.com" in result
        client.resolve_room_alias.assert_called_once_with("#banlist1:example.com")

    @pytest.mark.asyncio
    async def test_get_banlist_roomids_alias_error(self):
        """Test getting banlist room IDs with alias resolution error."""
        client = Mock()
        client.resolve_room_alias = AsyncMock(side_effect=Exception("Network error"))
        
        banlists = ["#banlist1:example.com", "!room2:example.com"]
        logger = Mock()
        
        result = await get_banlist_roomids(client, banlists, logger)
        
        assert "!room2:example.com" in result
        assert "!room1:example.com" not in result
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_ban_user_from_rooms_success(self):
        """Test successful user banning from rooms."""
        client = Mock()
        client.ban_user = AsyncMock()
        client.get_state_event = AsyncMock(return_value={"name": "Test Room"})
        
        roomlist = ["!room1:example.com", "!room2:example.com"]
        logger = Mock()
        
        result = await ban_user_from_rooms(
            client, "@user:example.com", roomlist, "banned", False, False, None, None, 0.1, logger
        )
        
        assert "ban_list" in result
        assert "error_list" in result
        assert "@user:example.com" in result["ban_list"]
        assert len(result["ban_list"]["@user:example.com"]) == 2

    @pytest.mark.asyncio
    async def test_ban_user_from_rooms_with_redaction(self):
        """Test user banning with message redaction."""
        client = Mock()
        client.ban_user = AsyncMock()
        client.get_state_event = AsyncMock(return_value={"name": "Test Room"})
        
        # Mock message redaction
        mock_msg = Mock()
        mock_msg.event_id = "!msg123:example.com"
        get_messages_func = AsyncMock(return_value=[mock_msg])
        
        database = Mock()
        database.execute = AsyncMock()
        
        roomlist = ["!room1:example.com"]
        logger = Mock()
        
        result = await ban_user_from_rooms(
            client, "@user:example.com", roomlist, "banned", False, True, 
            get_messages_func, database, 0.1, logger
        )
        
        assert "ban_list" in result
        database.execute.assert_called()

    @pytest.mark.asyncio
    async def test_ban_user_from_rooms_error(self):
        """Test user banning with errors."""
        client = Mock()
        client.ban_user = AsyncMock(side_effect=Exception("Ban failed"))
        client.get_state_event = AsyncMock(return_value={"name": "Test Room"})
        
        roomlist = ["!room1:example.com"]
        logger = Mock()
        
        result = await ban_user_from_rooms(
            client, "@user:example.com", roomlist, "banned", False, False, None, None, 0.1, logger
        )
        
        assert "error_list" in result
        assert "@user:example.com" in result["error_list"]

    @pytest.mark.asyncio
    async def test_user_permitted_unlimited_power(self):
        """Test user permission check with unlimited power."""
        client = Mock()
        
        with patch('community.helpers.room_utils.user_has_unlimited_power') as mock_unlimited:
            mock_unlimited.return_value = True
            
            result = await user_permitted(client, "@user:example.com", "!parent:example.com", 50, None, None)
            
            assert result == True

    @pytest.mark.asyncio
    async def test_user_permitted_sufficient_level(self):
        """Test user permission check with sufficient power level."""
        client = Mock()
        
        with patch('community.helpers.room_utils.user_has_unlimited_power') as mock_unlimited:
            mock_unlimited.return_value = False
            
            power_levels = Mock()
            power_levels.get_user_level.return_value = 75
            
            client.get_state_event = AsyncMock(return_value=power_levels)
            
            result = await user_permitted(client, "@user:example.com", "!parent:example.com", 50, None, None)
            
            assert result == True

    @pytest.mark.asyncio
    async def test_user_permitted_insufficient_level(self):
        """Test user permission check with insufficient power level."""
        client = Mock()
        
        with patch('community.helpers.room_utils.user_has_unlimited_power') as mock_unlimited:
            mock_unlimited.return_value = False
            
            power_levels = Mock()
            power_levels.get_user_level.return_value = 25
            
            client.get_state_event = AsyncMock(return_value=power_levels)
            
            result = await user_permitted(client, "@user:example.com", "!parent:example.com", 50, None, None)
            
            assert result == False

    @pytest.mark.asyncio
    async def test_user_permitted_error(self):
        """Test user permission check with error."""
        client = Mock()
        
        with patch('community.helpers.room_utils.user_has_unlimited_power') as mock_unlimited:
            mock_unlimited.side_effect = Exception("Network error")
            
            logger = Mock()
            result = await user_permitted(client, "@user:example.com", "!parent:example.com", 50, None, logger)
            
            assert result == False
            logger.error.assert_called()


def _membership_client(memberships, ban_side_effect=None):
    """Client whose member state per room comes from ``memberships``.

    Values are a Membership, or an exception instance to raise.
    """
    client = Mock()
    client.ban_user = AsyncMock(side_effect=ban_side_effect)

    async def get_state_event(room, event_type, state_key=None):
        if event_type == "m.room.name":
            return {"name": f"name-{room}"}
        value = memberships[room]
        if isinstance(value, Exception):
            raise value
        member = Mock()
        member.membership = value
        return member

    client.get_state_event = AsyncMock(side_effect=get_state_event)
    return client


class TestProactiveBanFiltering:
    """ban_user_from_rooms behaviour relied on by proactive (banlist) bans."""

    USER = "@spammer:example.com"

    async def _ban(self, client, rooms, **kwargs):
        return await ban_user_from_rooms(
            client, self.USER, rooms, "banned", False, False, None, None, 0, Mock(),
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_members_only_bans_active_memberships(self):
        memberships = {
            "!join": Membership.JOIN,
            "!invite": Membership.INVITE,
            "!knock": Membership.KNOCK,
            "!leave": Membership.LEAVE,
            "!ban": Membership.BAN,
        }
        client = _membership_client(memberships)

        result = await self._ban(client, list(memberships), members_only=True)

        banned_rooms = [c.args[0] for c in client.ban_user.call_args_list]
        assert banned_rooms == ["!join", "!invite", "!knock"]
        assert result["ban_list"][self.USER] == [
            "name-!join", "name-!invite", "name-!knock"
        ]
        assert result["error_list"] == {}

    @pytest.mark.asyncio
    async def test_without_members_only_former_members_are_banned(self):
        client = _membership_client({"!leave": Membership.LEAVE})

        result = await self._ban(client, ["!leave"])

        client.ban_user.assert_called_once()
        assert result["ban_list"][self.USER] == ["name-!leave"]

    @pytest.mark.asyncio
    async def test_never_member_skipped_without_redaction_scan(self):
        client = _membership_client({"!room": MNotFound(404, "not found")})
        get_messages = AsyncMock(return_value=[])
        database = Mock()
        database.execute = AsyncMock()

        result = await ban_user_from_rooms(
            client, self.USER, ["!room"], "banned", False, True,
            get_messages, database, 0, Mock(), members_only=True,
        )

        client.ban_user.assert_not_called()
        get_messages.assert_not_called()
        assert result["ban_list"][self.USER] == []
        assert result["error_list"] == {}

    @pytest.mark.asyncio
    async def test_membership_lookup_failure_is_skipped_not_error(self):
        # e.g. homeserver timeout, or the bot isn't joined to the room (403)
        client = _membership_client({
            "!timeout": TimeoutError(),
            "!forbidden": Exception("not in room"),
        })
        logger = Mock()

        result = await ban_user_from_rooms(
            client, self.USER, ["!timeout", "!forbidden"], "banned", False, False,
            None, None, 0, logger, members_only=True,
        )

        client.ban_user.assert_not_called()
        assert result["error_list"] == {}
        assert logger.warning.call_count == 2
        # The exception type is logged even when str(e) is empty
        assert "TimeoutError" in logger.warning.call_args_list[0].args[0]

    @pytest.mark.asyncio
    async def test_membership_lookup_failure_still_bans_when_not_proactive(self):
        # on-join / non-proactive path: a transient lookup error must not let
        # a banlisted user through
        client = _membership_client({"!timeout": TimeoutError()})

        result = await self._ban(client, ["!timeout"])

        client.ban_user.assert_called_once()
        assert result["ban_list"][self.USER] == ["name-!timeout"]

    @pytest.mark.asyncio
    async def test_ban_failures_counted_per_room(self):
        client = _membership_client(
            {"!a": Membership.JOIN, "!b": Membership.JOIN},
            ban_side_effect=Exception("forbidden"),
        )

        result = await self._ban(client, ["!a", "!b"], members_only=True)

        assert result["error_list"][self.USER] == ["name-!a", "name-!b"]

    @pytest.mark.asyncio
    async def test_all_rooms_skips_membership_check(self):
        client = _membership_client({})

        result = await ban_user_from_rooms(
            client, self.USER, ["!x"], "banned", True, False, None, None, 0, Mock(),
            members_only=True,
        )

        client.ban_user.assert_called_once()
        assert result["ban_list"][self.USER] == ["name-!x"]


class TestResolveBanlists:

    @pytest.mark.asyncio
    async def test_maps_room_ids_to_labels(self):
        client = Mock()
        client.resolve_room_alias = AsyncMock(return_value={"room_id": "!bl1:example.com"})

        result = await resolve_banlists(
            client, ["#bl:example.com", "!bl2:example.com"], Mock()
        )

        assert result == {
            "!bl1:example.com": "#bl:example.com",
            "!bl2:example.com": "!bl2:example.com",
        }

    @pytest.mark.asyncio
    async def test_failed_alias_omitted(self):
        client = Mock()
        client.resolve_room_alias = AsyncMock(side_effect=Exception("boom"))
        logger = Mock()

        result = await resolve_banlists(client, ["#bl:example.com"], logger)

        assert result == {}
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_failures_reported(self):
        client = Mock()
        client.resolve_room_alias = AsyncMock(side_effect=Exception("boom"))
        failures = []

        await resolve_banlists(client, ["#bl:example.com", "!ok:example.com"], Mock(), failures)

        assert failures == ["#bl:example.com"]
