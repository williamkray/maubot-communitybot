"""Tests for message utility functions."""

import pytest
from unittest.mock import Mock
from mautrix.types import MessageType, MediaMessageEventContent

from community.helpers.message_utils import (
    flag_message, flag_instaban, censor_room, 
    sanitize_room_name, generate_community_slug
)


class TestMessageUtils:
    """Test cases for message utility functions."""

    def test_flag_message_file_types(self):
        """Test that file messages are flagged when censor_files is True."""
        msg = Mock()
        msg.content.msgtype = MessageType.FILE
        msg.content.body = "test file"
        
        assert flag_message(msg, [], True) == True
        assert flag_message(msg, [], False) == False

    def test_flag_message_wordlist(self):
        """Test that messages are flagged based on wordlist patterns."""
        msg = Mock()
        msg.content.msgtype = MessageType.TEXT
        msg.content.body = "This is a test message with badword"
        
        wordlist = [r"badword", r"another.*pattern"]
        
        assert flag_message(msg, wordlist, False) == True
        
        msg.content.body = "This is a clean message"
        assert flag_message(msg, wordlist, False) == False

    def test_flag_message_invalid_regex(self):
        """Test that invalid regex patterns are handled gracefully."""
        msg = Mock()
        msg.content.msgtype = MessageType.TEXT
        msg.content.body = "test message"
        
        wordlist = [r"valid.*pattern", r"[invalid", r"another.*pattern"]
        
        # Should not raise exception and should work with valid patterns
        result = flag_message(msg, wordlist, False)
        assert isinstance(result, bool)

    def test_flag_instaban(self):
        """Test instant ban flagging."""
        msg = Mock()
        msg.content.msgtype = MessageType.TEXT
        msg.content.body = "This contains instaban_word"
        
        instaban_list = [r"instaban_word", r"another.*instaban"]
        
        assert flag_instaban(msg, instaban_list) == True
        
        msg.content.body = "This is clean"
        assert flag_instaban(msg, instaban_list) == False

    def test_censor_room_boolean_config(self):
        """Test room censoring with boolean configuration."""
        msg = Mock()
        msg.room_id = "!room123:example.com"
        
        assert censor_room(msg, True) == True
        assert censor_room(msg, False) == False

    def test_censor_room_list_config(self):
        """Test room censoring with list configuration."""
        msg = Mock()
        msg.room_id = "!room123:example.com"
        
        censor_list = ["!room123:example.com", "!room456:example.com"]
        
        assert censor_room(msg, censor_list) == True
        
        msg.room_id = "!room789:example.com"
        assert censor_room(msg, censor_list) == False

    def test_censor_room_invalid_config(self):
        """Test room censoring with invalid configuration."""
        msg = Mock()
        msg.room_id = "!room123:example.com"
        
        assert censor_room(msg, "invalid") == False
        assert censor_room(msg, None) == False

    def test_sanitize_room_name(self):
        """Test room name sanitization."""
        assert sanitize_room_name("Test Room 123") == "testroom123"
        assert sanitize_room_name("Special@#$%Characters") == "specialcharacters"
        assert sanitize_room_name("UPPERCASE") == "uppercase"
        assert sanitize_room_name("123 Numbers") == "123numbers"
        assert sanitize_room_name("") == ""

    def test_generate_community_slug(self):
        """Test community slug generation."""
        assert generate_community_slug("Test Community") == "tc"
        assert generate_community_slug("My Awesome Community") == "mac"
        assert generate_community_slug("Single") == "s"
        assert generate_community_slug("Multiple   Spaces") == "ms"
        assert generate_community_slug("") == ""
        assert generate_community_slug("   ") == ""
