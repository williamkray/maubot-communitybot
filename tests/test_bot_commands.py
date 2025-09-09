"""Tests for bot command handlers."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from mautrix.types import EventType, UserID, MessageEvent, StateEvent
from mautrix.errors import MNotFound

from community.bot import CommunityBot


class TestBotCommands:
    """Test cases for bot command handlers."""

    @pytest.fixture
    def bot(self):
        """Create a mock bot instance for testing."""
        bot = Mock(spec=CommunityBot)
        bot.client = Mock()
        bot.database = Mock()
        bot.log = Mock()
        bot.config = {
            "parent_room": "!parent:example.com",
            "community_slug": "test",
            "track_users": True,
            "warn_threshold_days": 7,
            "kick_threshold_days": 14,
            "sleep": 0.1,
            "censor_wordlist": [r"badword"],
            "censor_files": False,
            "censor": True,
            "banlists": ["!banlist:example.com"],
            "redact_on_ban": False,
            "admins": [],
            "moderators": []
        }
        return bot

    @pytest.fixture
    def mock_evt(self):
        """Create a mock MessageEvent for testing."""
        evt = Mock(spec=MessageEvent)
        evt.sender = "@user:example.com"
        evt.room_id = "!room:example.com"
        evt.reply = AsyncMock()
        evt.respond = AsyncMock()
        return evt

    @pytest.mark.asyncio
    async def test_check_parent_room_configured(self, bot, mock_evt):
        """Test check_parent_room when parent room is configured."""
        # Use the mock bot instance
        bot.config = {"parent_room": "!parent:example.com"}
        
        result = await bot.check_parent_room(mock_evt)
        
        assert result == True
        mock_evt.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_parent_room_not_configured(self, bot, mock_evt):
        """Test check_parent_room when parent room is not configured."""
        bot.config = {"parent_room": None}
        
        result = await bot.check_parent_room(mock_evt)
        
        assert result == False
        mock_evt.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_banlists_command(self, bot, mock_evt):
        """Test the check_banlists command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock the check_if_banned method
        with patch.object(real_bot, 'check_if_banned', return_value=True):
            await real_bot.check_banlists(mock_evt, "@test:example.com")
        
        mock_evt.reply.assert_called_once_with("user on banlist: True")

    @pytest.mark.asyncio
    async def test_sync_space_members_command(self, bot, mock_evt):
        """Test the sync_space_members command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'do_sync', return_value={"added": [], "dropped": []}):
            
            await real_bot.sync_space_members(mock_evt)
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_sync_space_members_no_permission(self, bot, mock_evt):
        """Test sync_space_members command without permission."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'user_permitted', return_value=False):
            await real_bot.sync_space_members(mock_evt)
        
        mock_evt.reply.assert_called_once_with("You don't have permission to use this command")

    @pytest.mark.asyncio
    async def test_sync_space_members_tracking_disabled(self, bot, mock_evt):
        """Test sync_space_members command when tracking is disabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = {**bot.config, "track_users": False}
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'user_permitted', return_value=True):
            await real_bot.sync_space_members(mock_evt)
        
        mock_evt.respond.assert_called_once_with("user tracking is disabled")

    @pytest.mark.asyncio
    async def test_ignore_command(self, bot, mock_evt):
        """Test the ignore command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock database operations
        real_bot.database.execute = AsyncMock()
        
        with patch.object(real_bot, 'user_permitted', return_value=True):
            await real_bot.ignore_user(mock_evt, "@test:example.com")
        
        real_bot.database.execute.assert_called()
        mock_evt.reply.assert_called()

    @pytest.mark.asyncio
    async def test_unignore_command(self, bot, mock_evt):
        """Test the unignore command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock database operations
        real_bot.database.execute = AsyncMock()
        
        with patch.object(real_bot, 'user_permitted', return_value=True):
            await real_bot.unignore_user(mock_evt, "@test:example.com")
        
        real_bot.database.execute.assert_called()
        mock_evt.reply.assert_called()

    @pytest.mark.asyncio
    async def test_kick_command(self, bot, mock_evt):
        """Test the kick command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]), \
             patch.object(real_bot, 'ban_this_user', return_value={"ban_list": {}, "error_list": {}}):
            
            await real_bot.kick_user(mock_evt, "@test:example.com")
        
        mock_evt.reply.assert_called()

    @pytest.mark.asyncio
    async def test_ban_command(self, bot, mock_evt):
        """Test the ban command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]), \
             patch.object(real_bot, 'ban_this_user', return_value={"ban_list": {}, "error_list": {}}):
            
            await real_bot.ban_user(mock_evt, "@test:example.com")
        
        mock_evt.reply.assert_called()

    @pytest.mark.asyncio
    async def test_doctor_command(self, bot, mock_evt):
        """Test the doctor command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]):
            
            await real_bot.doctor(mock_evt)
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_doctor_room_detail_command(self, bot, mock_evt):
        """Test the doctor room detail command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, '_doctor_room_detail', return_value=None):
            
            await real_bot.doctor_room_detail(mock_evt, "!room:example.com")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_initialize_command(self, bot, mock_evt):
        """Test the initialize command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'create_space', return_value=("!space:example.com", "#space:example.com")):
            
            await real_bot.initialize(mock_evt, "Test Community")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_create_room_command(self, bot, mock_evt):
        """Test the create_room command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'validate_room_aliases', return_value=(True, [])), \
             patch.object(real_bot, 'create_room', return_value=("!room:example.com", "#room:example.com")):
            
            await real_bot.create_room(mock_evt, "Test Room")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_create_room_command_alias_conflict(self, bot, mock_evt):
        """Test create_room command with alias conflict."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'validate_room_aliases', return_value=(False, ["#conflict:example.com"])):
            
            await real_bot.create_room(mock_evt, "Test Room")
        
        mock_evt.respond.assert_called()
        # Should mention the conflict

    @pytest.mark.asyncio
    async def test_archive_room_command(self, bot, mock_evt):
        """Test the archive_room command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'do_archive_room', return_value=None):
            
            await real_bot.archive_room(mock_evt, "!room:example.com")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_remove_room_command(self, bot, mock_evt):
        """Test the remove_room command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'remove_room_aliases', return_value=[]):
            
            await real_bot.remove_room(mock_evt, "!room:example.com")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_join_room_command(self, bot, mock_evt):
        """Test the join_room command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'join_room', return_value="!room:example.com"):
            
            await real_bot.join_room(mock_evt, "!room:example.com")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_leave_room_command(self, bot, mock_evt):
        """Test the leave_room command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'leave_room', return_value=None):
            
            await real_bot.leave_room(mock_evt, "!room:example.com")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_verify_command(self, bot, mock_evt):
        """Test the verify command."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'create_verification_dm', return_value="!dm:example.com"):
            
            await real_bot.verify_user(mock_evt, "@test:example.com", "!room:example.com")
        
        mock_evt.respond.assert_called()

    @pytest.mark.asyncio
    async def test_commands_require_permission(self, bot, mock_evt):
        """Test that commands require proper permissions."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.log = bot.log
        
        # Test various commands that require permission
        commands_to_test = [
            ('sync_space_members', []),
            ('ignore_user', ['@test:example.com']),
            ('unignore_user', ['@test:example.com']),
            ('kick_user', ['@test:example.com']),
            ('ban_user', ['@test:example.com']),
            ('doctor', []),
            ('doctor_room_detail', ['!room:example.com']),
            ('initialize', ['Test Community']),
            ('create_room', ['Test Room']),
            ('archive_room', ['!room:example.com']),
            ('remove_room', ['!room:example.com']),
            ('join_room', ['!room:example.com']),
            ('leave_room', ['!room:example.com']),
            ('verify_user', ['@test:example.com', '!room:example.com'])
        ]
        
        for command_name, args in commands_to_test:
            with patch.object(real_bot, 'user_permitted', return_value=False):
                command_func = getattr(real_bot, command_name)
                await command_func(mock_evt, *args)
                
                # Should respond with permission denied message
                mock_evt.reply.assert_called()
                mock_evt.reply.reset_mock()
