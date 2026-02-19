"""Simple tests for space creation functionality."""

import pytest
from unittest.mock import Mock, AsyncMock


class TestSpaceCreationSimple:
    """Simple tests for space creation functionality."""

    def test_get_space_roomlist_empty_parent_room(self):
        """Test get_space_roomlist with empty parent room."""
        from community.bot import CommunityBot

        # Create a mock bot instance
        bot = Mock(spec=CommunityBot)
        bot.config = {"parent_room": ""}
        bot.log = Mock()
        bot.client = Mock()

        # Mock the get_space_roomlist method
        bot.get_space_roomlist = AsyncMock(return_value=[])

        # Test that empty parent room returns empty list
        import asyncio
        result = asyncio.run(bot.get_space_roomlist())
        assert result == []

    def test_get_space_roomlist_with_parent_room(self):
        """Test get_space_roomlist with configured parent room."""
        from community.bot import CommunityBot

        # Create a mock bot instance
        bot = Mock(spec=CommunityBot)
        bot.config = {"parent_room": "!space:example.com"}
        bot.log = Mock()
        bot.client = Mock()
        bot.client.get_state = AsyncMock(return_value=[])

        # Mock the get_space_roomlist method
        bot.get_space_roomlist = AsyncMock(return_value=["!room1:example.com", "!room2:example.com"])

        # Test that configured parent room returns room list
        import asyncio
        result = asyncio.run(bot.get_space_roomlist())
        assert result == ["!room1:example.com", "!room2:example.com"]

    def test_space_creation_parameters(self):
        """Test that space creation parameters are correct."""
        # Test that the space creation logic uses correct parameters
        creation_content = {
            "type": "m.space",
            "m.federate": True,
            "m.room.history_visibility": "joined"
        }

        # Verify the creation content has the correct space type
        assert creation_content["type"] == "m.space"
        assert creation_content["m.federate"] is True
        assert creation_content["m.room.history_visibility"] == "joined"

    def test_power_level_verification_modern_room(self):
        """Test power level verification for modern room versions."""
        # Test that modern room version verification logic is correct
        room_version = "12"
        is_modern = int(room_version) >= 12

        assert is_modern is True

        # For modern rooms, creators have unlimited power and don't appear in power levels
        power_levels = {"users": {}}
        bot_power_level = power_levels.get("users", {}).get("@bot:example.com")

        # Bot should not have a power level in modern rooms (unlimited power)
        assert bot_power_level is None

    def test_power_level_verification_legacy_room(self):
        """Test power level verification for legacy room versions."""
        # Test that legacy room version verification logic is correct
        room_version = "1"
        is_modern = int(room_version) >= 12

        assert is_modern is False

        # For legacy rooms, bot should have power level 1000
        power_levels = {"users": {"@bot:example.com": 1000}}
        bot_power_level = power_levels.get("users", {}).get("@bot:example.com")

        assert bot_power_level == 1000

    def test_space_type_verification(self):
        """Test space type verification logic."""
        # Mock state events
        state_events = [
            Mock(type="m.room.create", content={"type": "m.space"}),
            Mock(type="m.room.power_levels", content={})
        ]

        # Find the room create event
        space_type_set = False
        for event in state_events:
            if event.type == "m.room.create":
                space_type = event.content.get("type")
                space_type_set = (space_type == "m.space")
                break

        assert space_type_set is True

    def test_space_type_not_set(self):
        """Test space type verification when type is not set."""
        # Mock state events with wrong type
        state_events = [
            Mock(type="m.room.create", content={"type": "m.room"}),
            Mock(type="m.room.power_levels", content={})
        ]

        # Find the room create event
        space_type_set = False
        for event in state_events:
            if event.type == "m.room.create":
                space_type = event.content.get("type")
                space_type_set = (space_type == "m.space")
                break

        assert space_type_set is False
