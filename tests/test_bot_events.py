"""Tests for bot event handlers."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from mautrix.types import EventType, UserID, MessageEvent, StateEvent, ReactionEvent
from mautrix.errors import MNotFound

from community.bot import CommunityBot


class TestBotEvents:
    """Test cases for bot event handlers."""

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
            "track_messages": True,
            "track_reactions": True,
            "warn_threshold_days": 7,
            "kick_threshold_days": 14,
            "sleep": 0.1,
            "censor_wordlist": [r"badword"],
            "censor_files": False,
            "censor": True,
            "banlists": ["!banlist:example.com"],
            "redact_on_ban": False,
            "proactive_banning": True,
            "check_if_human": True,
            "verification_phrases": ["test phrase"],
            "verification_attempts": 3,
            "verification_message": "Please verify",
            "invite_power_level": 50,
            "uncensor_pl": 50
        }
        return bot

    @pytest.fixture
    def mock_message_evt(self):
        """Create a mock MessageEvent for testing."""
        evt = Mock(spec=MessageEvent)
        evt.sender = "@user:example.com"
        evt.room_id = "!room:example.com"
        evt.timestamp = 1234567890
        evt.content = Mock()
        evt.content.body = "test message"
        evt.content.msgtype = "m.text"
        evt.reply = AsyncMock()
        evt.respond = AsyncMock()
        evt.react = AsyncMock()
        return evt

    @pytest.fixture
    def mock_state_evt(self):
        """Create a mock StateEvent for testing."""
        evt = Mock(spec=StateEvent)
        evt.sender = "@user:example.com"
        evt.room_id = "!room:example.com"
        evt.state_key = "@user:example.com"
        evt.content = {
            "entity": "@banned:example.com",
            "recommendation": "ban"
        }
        evt.prev_content = {}
        return evt

    @pytest.fixture
    def mock_reaction_evt(self):
        """Create a mock ReactionEvent for testing."""
        evt = Mock(spec=ReactionEvent)
        evt.sender = "@user:example.com"
        evt.room_id = "!room:example.com"
        evt.content = Mock()
        evt.content.relates_to = Mock()
        evt.content.relates_to.event_id = "!msg:example.com"
        evt.content.relates_to.key = "👍"
        return evt

    @pytest.mark.asyncio
    async def test_check_ban_event_proactive_banning_enabled(self, bot, mock_state_evt):
        """Test ban event handler with proactive banning enabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock required methods
        with patch.object(real_bot, 'get_banlist_roomids', return_value=["!banlist:example.com"]), \
             patch.object(real_bot, 'ban_this_user', return_value={"ban_list": {}, "error_list": {}}):
            
            await real_bot.check_ban_event(mock_state_evt)
        
        # Should call ban_this_user
        real_bot.ban_this_user.assert_called_once_with("@banned:example.com")

    @pytest.mark.asyncio
    async def test_check_ban_event_proactive_banning_disabled(self, bot, mock_state_evt):
        """Test ban event handler with proactive banning disabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = {**bot.config, "proactive_banning": False}
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'get_banlist_roomids', return_value=["!banlist:example.com"]):
            await real_bot.check_ban_event(mock_state_evt)
        
        # Should not call ban_this_user
        real_bot.ban_this_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_ban_event_wrong_room(self, bot, mock_state_evt):
        """Test ban event handler with wrong room."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'get_banlist_roomids', return_value=["!other:example.com"]):
            await real_bot.check_ban_event(mock_state_evt)
        
        # Should not call ban_this_user
        real_bot.ban_this_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_power_levels(self, bot, mock_state_evt):
        """Test power levels sync event handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        # Mock power level changes
        mock_state_evt.prev_content = {"users": {"@user:example.com": 25}}
        mock_state_evt.content = {"users": {"@user:example.com": 50}}
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com", "!room2:example.com"]), \
             patch.object(real_bot, 'sync_power_levels_to_room', return_value=None):
            
            await real_bot.sync_power_levels(mock_state_evt)
        
        # Should sync to all rooms
        assert real_bot.sync_power_levels_to_room.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_power_levels_wrong_room(self, bot, mock_state_evt):
        """Test power levels sync with wrong room."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.log = bot.log
        
        mock_state_evt.room_id = "!other:example.com"
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]):
            await real_bot.sync_power_levels(mock_state_evt)
        
        # Should not sync to any rooms
        real_bot.sync_power_levels_to_room.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_leave_events(self, bot, mock_state_evt):
        """Test leave events handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock database operations
        real_bot.database.execute = AsyncMock()
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com", "!room2:example.com"]):
            await real_bot.handle_leave_events(mock_state_evt)
        
        # Should delete user from database
        real_bot.database.execute.assert_called()

    @pytest.mark.asyncio
    async def test_handle_leave(self, bot, mock_state_evt):
        """Test leave event handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'handle_leave_events', return_value=None):
            await real_bot.handle_leave(mock_state_evt)
        
        real_bot.handle_leave_events.assert_called_once_with(mock_state_evt)

    @pytest.mark.asyncio
    async def test_handle_kick(self, bot, mock_state_evt):
        """Test kick event handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'handle_leave_events', return_value=None):
            await real_bot.handle_kick(mock_state_evt)
        
        real_bot.handle_leave_events.assert_called_once_with(mock_state_evt)

    @pytest.mark.asyncio
    async def test_handle_ban(self, bot, mock_state_evt):
        """Test ban event handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'handle_leave_events', return_value=None):
            await real_bot.handle_ban(mock_state_evt)
        
        real_bot.handle_leave_events.assert_called_once_with(mock_state_evt)

    @pytest.mark.asyncio
    async def test_newjoin_event(self, bot, mock_state_evt):
        """Test new join event handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock database operations
        real_bot.database.execute = AsyncMock()
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com", "!room2:example.com"]), \
             patch.object(real_bot, 'upsert_user_timestamp', return_value=None):
            
            await real_bot.newjoin(mock_state_evt)
        
        # Should update user timestamp
        real_bot.upsert_user_timestamp.assert_called()


    @pytest.mark.asyncio
    async def test_newjoin_notification_supports_user_link(self, bot, mock_state_evt):
        """Test join notification formatting with user_link placeholder."""
        from community.bot import CommunityBot
        real_bot = CommunityBot()
        real_bot.config = {
            **bot.config,
            "greeting_rooms": {"!room:example.com": "none"},
            "greetings": {},
            "notification_room": "!notifications:example.com",
            "join_notification_message": "{user_link} joined {room_link} ({room_id})"
        }
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log

        real_bot.client.parse_user_id = Mock(return_value=("alice", "example.com"))
        room_name_state = Mock()
        room_name_state.name = "Test Room"
        real_bot.client.get_state_event = AsyncMock(return_value=room_name_state)
        real_bot.client.send_notice = AsyncMock()
        real_bot.database.execute = AsyncMock()

        mock_state_evt.source = 0

        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room:example.com"]), \
             patch.object(real_bot, 'check_if_banned', return_value=False), \
             patch.object(real_bot, 'upsert_user_timestamp', return_value=None):
            await real_bot.newjoin(mock_state_evt)

        real_bot.client.send_notice.assert_called_once()
        args, kwargs = real_bot.client.send_notice.call_args
        assert args[0] == "!notifications:example.com"
        assert args[1] == "https://matrix.to/#/@user:example.com joined https://matrix.to/#/!room:example.com (!room:example.com)"
        assert "https://matrix.to/#/@user:example.com" in kwargs["html"]
        assert ">alice</a>" in kwargs["html"]
        assert "https://matrix.to/#/!room:example.com" in kwargs["html"]
        assert ">Test Room</a>" in kwargs["html"]
        assert "(!room:example.com)" in kwargs["html"]

    @pytest.mark.asyncio
    async def test_update_message_timestamp_tracking_enabled(self, bot, mock_message_evt):
        """Test message timestamp update with tracking enabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock power levels
        power_levels = Mock()
        power_levels.get_user_level.return_value = 25
        
        real_bot.client.get_state_event = AsyncMock(return_value=power_levels)
        real_bot.database.execute = AsyncMock()
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]), \
             patch.object(real_bot, 'upsert_user_timestamp', return_value=None):
            
            await real_bot.update_message_timestamp(mock_message_evt)
        
        # Should update user timestamp
        real_bot.upsert_user_timestamp.assert_called()

    @pytest.mark.asyncio
    async def test_update_message_timestamp_tracking_disabled(self, bot, mock_message_evt):
        """Test message timestamp update with tracking disabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = {**bot.config, "track_messages": False}
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]):
            await real_bot.update_message_timestamp(mock_message_evt)
        
        # Should not update user timestamp
        real_bot.upsert_user_timestamp.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_verification(self, bot, mock_message_evt):
        """Test verification message handler."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock verification state
        verification_state = {
            "user_id": "@user:example.com",
            "target_room_id": "!room:example.com",
            "verification_phrase": "test phrase",
            "attempts_remaining": 3,
            "required_power_level": 50
        }
        
        real_bot.database.fetchrow = AsyncMock(return_value=verification_state)
        real_bot.database.execute = AsyncMock()
        
        # Mock message content
        mock_message_evt.content.body = "test phrase"
        
        with patch.object(real_bot, 'user_permitted', return_value=True), \
             patch.object(real_bot, 'join_room', return_value="!room:example.com"):
            
            await real_bot.handle_verification(mock_message_evt)
        
        # Should process verification
        real_bot.database.execute.assert_called()

    @pytest.mark.asyncio
    async def test_handle_verification_wrong_phrase(self, bot, mock_message_evt):
        """Test verification with wrong phrase."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock verification state
        verification_state = {
            "user_id": "@user:example.com",
            "target_room_id": "!room:example.com",
            "verification_phrase": "correct phrase",
            "attempts_remaining": 3,
            "required_power_level": 50
        }
        
        real_bot.database.fetchrow = AsyncMock(return_value=verification_state)
        real_bot.database.execute = AsyncMock()
        
        # Mock message content with wrong phrase
        mock_message_evt.content.body = "wrong phrase"
        
        await real_bot.handle_verification(mock_message_evt)
        
        # Should decrement attempts
        real_bot.database.execute.assert_called()

    @pytest.mark.asyncio
    async def test_handle_verification_no_state(self, bot, mock_message_evt):
        """Test verification with no verification state."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock no verification state
        real_bot.database.fetchrow = AsyncMock(return_value=None)
        
        await real_bot.handle_verification(mock_message_evt)
        
        # Should not process verification
        real_bot.database.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_reaction_tracking_enabled(self, bot, mock_reaction_evt):
        """Test reaction event handler with tracking enabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        # Mock power levels
        power_levels = Mock()
        power_levels.get_user_level.return_value = 25
        
        real_bot.client.get_state_event = AsyncMock(return_value=power_levels)
        real_bot.database.execute = AsyncMock()
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]), \
             patch.object(real_bot, 'upsert_user_timestamp', return_value=None):
            
            await real_bot.handle_reaction(mock_reaction_evt)
        
        # Should update user timestamp
        real_bot.upsert_user_timestamp.assert_called()

    @pytest.mark.asyncio
    async def test_handle_reaction_tracking_disabled(self, bot, mock_reaction_evt):
        """Test reaction event handler with tracking disabled."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = {**bot.config, "track_reactions": False}
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!room1:example.com"]):
            await real_bot.handle_reaction(mock_reaction_evt)
        
        # Should not update user timestamp
        real_bot.upsert_user_timestamp.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_reaction_wrong_room(self, bot, mock_reaction_evt):
        """Test reaction event handler with wrong room."""
        from community.bot import CommunityBot
        
        real_bot = CommunityBot()
        real_bot.config = bot.config
        real_bot.client = bot.client
        real_bot.database = bot.database
        real_bot.log = bot.log
        
        with patch.object(real_bot, 'get_space_roomlist', return_value=["!other:example.com"]):
            await real_bot.handle_reaction(mock_reaction_evt)
        
        # Should not update user timestamp
        real_bot.upsert_user_timestamp.assert_not_called()


    @pytest.mark.asyncio
    async def test_sleep_if_configured_uses_asyncio_sleep(self):
        """Test that configured delays use asyncio.sleep without blocking."""
        real_bot = CommunityBot()
        with patch("community.bot.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await real_bot._sleep_if_configured(1.5)
            sleep_mock.assert_awaited_once_with(1.5)

    @pytest.mark.asyncio
    async def test_newjoin_uses_async_delay_for_greeting(self, bot, mock_state_evt):
        """Test that join greetings use the async delay helper."""
        real_bot = CommunityBot()
        real_bot.client = Mock()
        real_bot.client.parse_user_id.return_value = ("alice", "example.com")
        real_bot.client.send_notice = AsyncMock()
        real_bot.client.get_state_event = AsyncMock(return_value=Mock(name="Test Room"))
        real_bot.database = bot.database
        real_bot.log = bot.log
        real_bot.config = {
            **bot.config,
            "parent_room": "!parent:example.com",
            "greeting_rooms": {"!room:example.com": "default"},
            "greetings": {"default": "Welcome {user}"},
            "welcome_sleep": 2,
            "notification_room": "!notif:example.com",
            "join_notification_message": "{user_link} joined {room_link}",
        }
        real_bot._sleep_if_configured = AsyncMock()
        real_bot.check_if_banned = AsyncMock(return_value=False)
        real_bot.ban_this_user = AsyncMock()
        real_bot.do_sync = AsyncMock()
        real_bot.get_space_roomlist = AsyncMock(return_value=["!room:example.com"])

        await real_bot.newjoin(mock_state_evt)

        real_bot._sleep_if_configured.assert_awaited_once_with(2)
        assert real_bot.client.send_notice.await_count == 2

    @pytest.mark.asyncio
    async def test_user_kick_uses_async_delay(self, bot):
        """Test that user kicking uses the async delay helper between rooms."""
        real_bot = CommunityBot()
        real_bot.client = Mock()
        real_bot.client.get_state_event = AsyncMock(side_effect=[
            {"name": "Room One"},
            {},  # membership lookup
        ])
        real_bot.client.kick_user = AsyncMock()
        real_bot.database = bot.database
        real_bot.log = bot.log
        real_bot.config = {**bot.config, "sleep": 0.5, "parent_room": "!room:example.com"}
        real_bot._sleep_if_configured = AsyncMock()

        evt = Mock(spec=MessageEvent)
        evt.mark_read = AsyncMock()
        evt.respond = AsyncMock()
        evt.reply = AsyncMock()

        with patch.object(real_bot, "get_space_roomlist", AsyncMock(return_value=[])):
            await real_bot.user_kick(evt, "@user:example.com")

        real_bot._sleep_if_configured.assert_awaited_once_with(0.5)

    def test_render_message_template_uses_consistent_user_placeholders(self):
        """{user} should stay plain while {user_link} becomes clickable."""
        real_bot = CommunityBot()

        plain_text, html_text = real_bot._render_message_template(
            "{user} | {user_link} | {room} | {room_link} | {room_id}",
            "@alice:example.com",
            "alice",
            "!room:example.com",
            "Test Room",
        )

        assert plain_text == (
            "@alice:example.com | https://matrix.to/#/@alice:example.com | "
            "Test Room | https://matrix.to/#/!room:example.com | !room:example.com"
        )
        assert "@alice:example.com" in html_text
        assert "<a href='https://matrix.to/#/@alice:example.com'>alice</a>" in html_text
        assert "<a href='https://matrix.to/#/!room:example.com'>Test Room</a>" in html_text

    def test_render_message_template_without_room_keeps_room_placeholders_safe(self):
        """Greeting templates without room data should not break placeholder rendering."""
        real_bot = CommunityBot()

        plain_text, html_text = real_bot._render_message_template(
            "Welcome {user} / {user_link}",
            "@alice:example.com",
            "alice",
        )

        assert plain_text == "Welcome @alice:example.com / https://matrix.to/#/@alice:example.com"
        assert html_text == (
            "Welcome @alice:example.com / "
            "<a href='https://matrix.to/#/@alice:example.com'>alice</a>"
        )

