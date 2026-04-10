"""Tests for database utility functions."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import asyncio

from community.helpers.database_utils import (
    get_messages_to_redact, redact_messages, upsert_user_timestamp,
    get_inactive_users, cleanup_stale_verification_states,
    get_verification_state, create_verification_state,
    update_verification_attempts, delete_verification_state
)


class TestDatabaseUtils:
    """Test cases for database utility functions."""

    @pytest.mark.asyncio
    async def test_get_messages_to_redact_success(self):
        """Test getting messages to redact successfully."""
        client = Mock()
        
        # Mock message events
        msg1 = Mock()
        msg1.content = Mock()
        msg1.content.serialize.return_value = {"body": "test"}
        
        msg2 = Mock()
        msg2.content = None
        
        msg3 = Mock()
        msg3.content = Mock()
        msg3.content.serialize.return_value = {"body": "test2"}
        
        messages = Mock()
        messages.events = [msg1, msg2, msg3]
        
        client.get_messages = AsyncMock(return_value=messages)
        
        logger = Mock()
        result = await get_messages_to_redact(client, "!room:example.com", "@user:example.com", logger)
        
        assert len(result) == 2  # Only msg1 and msg3 have content
        assert msg1 in result
        assert msg3 in result
        assert msg2 not in result

    @pytest.mark.asyncio
    async def test_get_messages_to_redact_error(self):
        """Test getting messages to redact with error."""
        client = Mock()
        client.get_messages = AsyncMock(side_effect=Exception("Network error"))
        
        logger = Mock()
        result = await get_messages_to_redact(client, "!room:example.com", "@user:example.com", logger)
        
        assert result == []
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_redact_messages_success(self):
        """Test redacting messages successfully."""
        client = Mock()
        client.redact = AsyncMock()
        
        database = Mock()
        database.fetch = AsyncMock(return_value=[
            {"event_id": "!msg1:example.com"},
            {"event_id": "!msg2:example.com"}
        ])
        database.execute = AsyncMock()
        
        logger = Mock()
        
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await redact_messages(client, database, "!room:example.com", 0.1, logger)
        
        assert result["success"] == 2
        assert result["failure"] == 0
        assert client.redact.call_count == 2
        assert database.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_redact_messages_rate_limited(self):
        """Test redacting messages with rate limiting."""
        client = Mock()
        client.redact = AsyncMock(side_effect=Exception("Too Many Requests"))
        
        database = Mock()
        database.fetch = AsyncMock(return_value=[
            {"event_id": "!msg1:example.com"}
        ])
        
        logger = Mock()
        
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await redact_messages(client, database, "!room:example.com", 0.1, logger)
        
        assert result["success"] == 0
        assert result["failure"] == 0  # Rate limited, so no failure count
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_upsert_user_timestamp_success(self):
        """Test upserting user timestamp successfully."""
        database = Mock()
        database.execute = AsyncMock()
        
        logger = Mock()
        
        await upsert_user_timestamp(database, "@user:example.com", 1234567890, logger)
        
        database.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_inactive_users_success(self):
        """Test getting inactive users successfully."""
        database = Mock()
        database.fetch = AsyncMock(side_effect=[
            [{"mxid": "@user1:example.com"}, {"mxid": "@user2:example.com"}],  # warn results
            [{"mxid": "@user3:example.com"}]  # kick results
        ])
        
        logger = Mock()
        
        with patch('time.time', return_value=1234567890):
            result = await get_inactive_users(database, 7, 14, logger)
        
        assert len(result["warn"]) == 2
        assert len(result["kick"]) == 1
        assert "@user1:example.com" in result["warn"]
        assert "@user3:example.com" in result["kick"]

    @pytest.mark.asyncio
    async def test_get_inactive_users_error(self):
        """Test getting inactive users with error."""
        database = Mock()
        database.fetch = AsyncMock(side_effect=Exception("Database error"))
        
        logger = Mock()
        
        result = await get_inactive_users(database, 7, 14, logger)
        
        assert result == {"warn": [], "kick": []}
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_stale_verification_states_success(self):
        """Test cleaning up stale verification states successfully."""
        database = Mock()
        database.execute = AsyncMock()
        
        logger = Mock()
        
        await cleanup_stale_verification_states(database, logger)
        
        database.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_stale_verification_states_error(self):
        """Test cleaning up stale verification states with error."""
        database = Mock()
        database.execute = AsyncMock(side_effect=Exception("Database error"))
        
        logger = Mock()
        
        await cleanup_stale_verification_states(database, logger)
        
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_get_verification_state_success(self):
        """Test getting verification state successfully."""
        database = Mock()
        database.fetchrow = AsyncMock(return_value={
            "dm_room_id": "!dm:example.com",
            "user_id": "@user:example.com",
            "target_room_id": "!room:example.com",
            "verification_phrase": "test phrase",
            "attempts_remaining": 3,
            "required_power_level": 50
        })
        
        result = await get_verification_state(database, "!dm:example.com")
        
        assert result is not None
        assert result["dm_room_id"] == "!dm:example.com"
        assert result["user_id"] == "@user:example.com"

    @pytest.mark.asyncio
    async def test_get_verification_state_not_found(self):
        """Test getting verification state when not found."""
        database = Mock()
        database.fetchrow = AsyncMock(return_value=None)
        
        result = await get_verification_state(database, "!dm:example.com")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_get_verification_state_error(self):
        """Test getting verification state with error."""
        database = Mock()
        database.fetchrow = AsyncMock(side_effect=Exception("Database error"))
        
        result = await get_verification_state(database, "!dm:example.com")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_create_verification_state_success(self):
        """Test creating verification state successfully."""
        database = Mock()
        database.execute = AsyncMock()
        
        await create_verification_state(
            database, "!dm:example.com", "@user:example.com", 
            "!room:example.com", "test phrase", 3, 50
        )
        
        database.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_verification_state_error(self):
        """Test creating verification state with error (should not raise)."""
        database = Mock()
        database.execute = AsyncMock(side_effect=Exception("Database error"))
        
        # Should not raise exception
        await create_verification_state(
            database, "!dm:example.com", "@user:example.com", 
            "!room:example.com", "test phrase", 3, 50
        )

    @pytest.mark.asyncio
    async def test_update_verification_attempts_success(self):
        """Test updating verification attempts successfully."""
        database = Mock()
        database.execute = AsyncMock()
        
        await update_verification_attempts(database, "!dm:example.com", 2)
        
        database.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_verification_state_success(self):
        """Test deleting verification state successfully."""
        database = Mock()
        database.execute = AsyncMock()
        
        await delete_verification_state(database, "!dm:example.com")
        
        database.execute.assert_called_once()
