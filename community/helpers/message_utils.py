"""Message and content utility functions."""

import re
from typing import Optional
from mautrix.types import MessageType, MediaMessageEventContent


def flag_message(msg, censor_wordlist: list, censor_files: bool) -> bool:
    """Check if a message should be flagged for censorship.
    
    Args:
        msg: The message event to check
        censor_wordlist: List of regex patterns to check against
        censor_files: Whether to flag file messages
        
    Returns:
        bool: True if message should be flagged
    """
    if msg.content.msgtype in [
        MessageType.FILE,
        MessageType.IMAGE,
        MessageType.VIDEO,
    ]:
        return censor_files

    for w in censor_wordlist:
        try:
            if bool(re.search(w, msg.content.body, re.IGNORECASE)):
                return True
        except Exception:
            # Skip invalid regex patterns
            pass
    
    return False


def flag_instaban(msg, instaban_wordlist: list) -> bool:
    """Check if a message should trigger an instant ban.
    
    Args:
        msg: The message event to check
        instaban_wordlist: List of regex patterns that trigger instant ban
        
    Returns:
        bool: True if message should trigger instant ban
    """
    for w in instaban_wordlist:
        try:
            if bool(re.search(w, msg.content.body, re.IGNORECASE)):
                return True
        except Exception:
            # Skip invalid regex patterns
            pass
    
    return False


def censor_room(msg, censor_config) -> bool:
    """Check if a message should be censored based on room configuration.
    
    Args:
        msg: The message event to check
        censor_config: Censor configuration (bool or list of room IDs)
        
    Returns:
        bool: True if message should be censored
    """
    if isinstance(censor_config, bool):
        return censor_config
    elif isinstance(censor_config, list):
        return msg.room_id in censor_config
    else:
        return False


def sanitize_room_name(room_name: str) -> str:
    """Sanitize a room name for use in aliases.
    
    Args:
        room_name: The room name to sanitize
        
    Returns:
        str: Sanitized room name (alphanumeric only, lowercase)
    """
    return re.sub(r"[^a-zA-Z0-9]", "", room_name).lower()


def generate_community_slug(community_name: str) -> str:
    """Generate a community slug from the community name.
    
    Args:
        community_name: The full community name
        
    Returns:
        str: A slug made from the first letter of each word, lowercase
    """
    words = community_name.strip().split()
    return ''.join(word[0].lower() for word in words if word)
