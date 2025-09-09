"""Tests for room utility functions."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from mautrix.types import EventType, PowerLevelStateEventContent
from mautrix.errors import MNotFound

from community.helpers.room_utils import (
    validate_room_alias, validate_room_aliases, get_room_version_and_creators,
    is_modern_room_version, user_has_unlimited_power, get_moderators_and_above
)


class TestRoomUtils:
    """Test cases for room utility functions."""

    @pytest.mark.asyncio
    async def test_validate_room_alias_exists(self):
        """Test alias validation when alias exists."""
        client = Mock()
        client.resolve_room_alias = AsyncMock()
        
        # Alias exists - should return False
        result = await validate_room_alias(client, "test", "example.com")
        assert result == False
        client.resolve_room_alias.assert_called_once_with("#test:example.com")

    @pytest.mark.asyncio
    async def test_validate_room_alias_not_exists(self):
        """Test alias validation when alias doesn't exist."""
        client = Mock()
        client.resolve_room_alias = AsyncMock(side_effect=MNotFound("Room not found", 404))
        
        # Alias doesn't exist - should return True
        result = await validate_room_alias(client, "test", "example.com")
        assert result == True

    @pytest.mark.asyncio
    async def test_validate_room_alias_error(self):
        """Test alias validation with error."""
        client = Mock()
        client.resolve_room_alias = AsyncMock(side_effect=Exception("Network error"))
        
        # Error should return True (assume available)
        result = await validate_room_alias(client, "test", "example.com")
        assert result == True

    @pytest.mark.asyncio
    async def test_validate_room_aliases_no_slug(self):
        """Test alias validation without community slug."""
        client = Mock()
        
        result = await validate_room_aliases(client, ["room1", "room2"], "", "example.com")
        assert result == (False, [])

    @pytest.mark.asyncio
    async def test_validate_room_aliases_success(self):
        """Test successful alias validation."""
        client = Mock()
        client.resolve_room_alias = AsyncMock(side_effect=MNotFound("Room not found", 404))
        
        result = await validate_room_aliases(client, ["room1", "room2"], "test", "example.com")
        assert result == (True, [])

    @pytest.mark.asyncio
    async def test_validate_room_aliases_conflicts(self):
        """Test alias validation with conflicts."""
        client = Mock()
        
        def resolve_side_effect(alias):
            if "room1" in alias:
                return {"room_id": "!room1:example.com"}  # Exists
            else:
                raise MNotFound()  # Doesn't exist
        
        client.resolve_room_alias = AsyncMock(side_effect=resolve_side_effect)
        
        result = await validate_room_aliases(client, ["room1", "room2"], "test", "example.com")
        assert result == (False, ["#room1-test:example.com"])

    @pytest.mark.asyncio
    async def test_get_room_version_and_creators_success(self):
        """Test getting room version and creators successfully."""
        client = Mock()
        
        # Mock state events
        create_event = Mock()
        create_event.type = EventType.ROOM_CREATE
        create_event.sender = "@creator:example.com"
        create_event.content = {
            "room_version": "12",
            "additional_creators": ["@creator2:example.com"]
        }
        
        other_event = Mock()
        other_event.type = EventType.ROOM_POWER_LEVELS
        
        client.get_state = AsyncMock(return_value=[create_event, other_event])
        
        version, creators = await get_room_version_and_creators(client, "!room:example.com")
        
        assert version == "12"
        assert "@creator:example.com" in creators
        assert "@creator2:example.com" in creators

    @pytest.mark.asyncio
    async def test_get_room_version_and_creators_no_create_event(self):
        """Test getting room version when no create event exists."""
        client = Mock()
        client.get_state = AsyncMock(return_value=[])
        
        version, creators = await get_room_version_and_creators(client, "!room:example.com")
        
        assert version == "1"
        assert creators == []

    @pytest.mark.asyncio
    async def test_get_room_version_and_creators_error(self):
        """Test getting room version with error."""
        client = Mock()
        client.get_state = AsyncMock(side_effect=Exception("Network error"))
        
        version, creators = await get_room_version_and_creators(client, "!room:example.com")
        
        assert version == "1"
        assert creators == []

    def test_is_modern_room_version(self):
        """Test modern room version detection."""
        assert is_modern_room_version("12") == True
        assert is_modern_room_version("13") == True
        assert is_modern_room_version("11") == False
        assert is_modern_room_version("1") == False
        assert is_modern_room_version("invalid") == False
        assert is_modern_room_version("") == False

    @pytest.mark.asyncio
    async def test_user_has_unlimited_power_modern_room(self):
        """Test unlimited power check in modern room."""
        client = Mock()
        
        with patch('community.helpers.room_utils.get_room_version_and_creators') as mock_get_version:
            mock_get_version.return_value = ("12", ["@user:example.com"])
            
            result = await user_has_unlimited_power(client, "@user:example.com", "!room:example.com")
            assert result == True
            
            result = await user_has_unlimited_power(client, "@other:example.com", "!room:example.com")
            assert result == False

    @pytest.mark.asyncio
    async def test_user_has_unlimited_power_old_room(self):
        """Test unlimited power check in old room."""
        client = Mock()
        
        with patch('community.helpers.room_utils.get_room_version_and_creators') as mock_get_version:
            mock_get_version.return_value = ("11", ["@user:example.com"])
            
            result = await user_has_unlimited_power(client, "@user:example.com", "!room:example.com")
            assert result == False

    @pytest.mark.asyncio
    async def test_user_has_unlimited_power_error(self):
        """Test unlimited power check with error."""
        client = Mock()
        
        with patch('community.helpers.room_utils.get_room_version_and_creators') as mock_get_version:
            mock_get_version.side_effect = Exception("Network error")
            
            result = await user_has_unlimited_power(client, "@user:example.com", "!room:example.com")
            assert result == False

    @pytest.mark.asyncio
    async def test_get_moderators_and_above_success(self):
        """Test getting moderators successfully."""
        client = Mock()
        
        power_levels = Mock()
        power_levels.users = {
            "@user1:example.com": 50,  # Moderator
            "@user2:example.com": 100,  # Admin
            "@user3:example.com": 25,  # Regular user
            "@user4:example.com": 75,  # Above moderator
        }
        
        client.get_state_event = AsyncMock(return_value=power_levels)
        
        moderators = await get_moderators_and_above(client, "!room:example.com")
        
        assert "@user1:example.com" in moderators
        assert "@user2:example.com" in moderators
        assert "@user4:example.com" in moderators
        assert "@user3:example.com" not in moderators

    @pytest.mark.asyncio
    async def test_get_moderators_and_above_error(self):
        """Test getting moderators with error."""
        client = Mock()
        client.get_state_event = AsyncMock(side_effect=Exception("Network error"))
        
        moderators = await get_moderators_and_above(client, "!room:example.com")
        
        assert moderators == []
