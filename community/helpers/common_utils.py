"""Common utility functions for bot operations."""

from typing import Optional, Dict, Any
from mautrix.types import EventType, MessageEvent


async def get_room_name(client, room_id: str, logger) -> Optional[str]:
    """Get room name from room ID.
    
    Args:
        client: Matrix client instance
        room_id: Room ID to get name for
        logger: Logger instance for error reporting
        
    Returns:
        str: Room name or None if not found/error
    """
    try:
        room_name_event = await client.get_state_event(room_id, EventType.ROOM_NAME)
        return room_name_event.name if room_name_event else None
    except Exception as e:
        logger.debug(f"Could not get room name for {room_id}: {e}")
        return None


async def get_room_power_levels(client, room_id: str, logger) -> Optional[Any]:
    """Get power levels for a room.
    
    Args:
        client: Matrix client instance
        room_id: Room ID to get power levels for
        logger: Logger instance for error reporting
        
    Returns:
        PowerLevelStateEventContent or None if error
    """
    try:
        return await client.get_state_event(room_id, EventType.ROOM_POWER_LEVELS)
    except Exception as e:
        logger.debug(f"Could not get power levels for {room_id}: {e}")
        return None


async def check_room_membership(client, room_id: str, user_id: str, logger) -> bool:
    """Check if a user is a member of a room.
    
    Args:
        client: Matrix client instance
        room_id: Room ID to check
        user_id: User ID to check
        logger: Logger instance for error reporting
        
    Returns:
        bool: True if user is a member, False otherwise
    """
    try:
        await client.get_state_event(room_id, EventType.ROOM_MEMBER, user_id)
        return True
    except Exception:
        return False


def format_room_info(room_id: str, room_name: Optional[str] = None) -> str:
    """Format room information for display.
    
    Args:
        room_id: Room ID
        room_name: Optional room name
        
    Returns:
        str: Formatted room info
    """
    if room_name:
        return f"{room_name} ({room_id})"
    return room_id


def safe_get(dictionary: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get a value from a dictionary with a default.
    
    Args:
        dictionary: Dictionary to get value from
        key: Key to look up
        default: Default value if key not found
        
    Returns:
        Value from dictionary or default
    """
    return dictionary.get(key, default) if dictionary else default
