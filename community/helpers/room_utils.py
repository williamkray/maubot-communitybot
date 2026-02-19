"""Room and space utility functions."""

from typing import Tuple, List
from mautrix.types import EventType
from mautrix.errors import MNotFound


async def validate_room_alias(client, alias_localpart: str, server: str) -> bool:
    """Check if a room alias already exists.

    Args:
        client: Matrix client instance
        alias_localpart: The localpart of the alias (without # and :server)
        server: The server domain

    Returns:
        bool: True if alias is available, False if it already exists
    """
    try:
        full_alias = f"#{alias_localpart}:{server}"
        await client.resolve_room_alias(full_alias)
        # If we get here, the alias exists
        return False
    except MNotFound:
        # Alias doesn't exist, so it's available
        return True
    except Exception as e:
        # For other errors, assume alias is available to be safe
        return True


async def validate_room_aliases(
    client, room_names: list[str], community_slug: str, server: str
) -> Tuple[bool, List[str]]:
    """Validate that all room aliases are available.

    Args:
        client: Matrix client instance
        room_names: List of room names to validate
        community_slug: The community slug to append
        server: The server domain

    Returns:
        tuple: (is_valid, list_of_conflicting_aliases)
    """
    if not community_slug:
        return False, []

    conflicting_aliases = []

    for room_name in room_names:
        # Clean the room name and create alias
        from .message_utils import sanitize_room_name

        sanitized_name = sanitize_room_name(room_name)
        alias_localpart = f"{sanitized_name}-{community_slug}"

        # Check if alias is available
        is_available = await validate_room_alias(client, alias_localpart, server)
        if not is_available:
            conflicting_aliases.append(f"#{alias_localpart}:{server}")

    return len(conflicting_aliases) == 0, conflicting_aliases


async def get_room_version_and_creators(
    client, room_id: str, logger=None
) -> Tuple[str, List[str]]:
    """Get the room version and creators for a room.

    Args:
        client: Matrix client instance
        room_id: The room ID to check

    Returns:
        tuple: (room_version, list_of_creators)
    """
    try:
        # Get all state events to find the creation event
        state_events = await client.get_state(room_id)

        # Find the m.room.create event
        creation_event = None
        for event in state_events:
            if event.type == EventType.ROOM_CREATE:
                creation_event = event
                break

        if not creation_event:
            # Default to version 1 if no creation event found
            return "1", []

        room_version = creation_event.content.get("room_version", "1")
        creators = []

        # Add the sender of the creation event as a creator
        if creation_event.sender:
            creators.append(creation_event.sender)

        # Add any additional creators from the content
        additional_creators = creation_event.content.get("additional_creators", [])
        if isinstance(additional_creators, list):
            creators.extend(additional_creators)

        return room_version, creators

    except Exception:
        # Default to version 1 if there's an error
        return "1", []


def is_modern_room_version(room_version: str) -> bool:
    """Check if a room version is 12 or newer (modern room versions).

    Args:
        room_version: The room version string to check

    Returns:
        bool: True if room version is 12 or newer
    """
    try:
        version_num = int(room_version)
        return version_num >= 12
    except (ValueError, TypeError):
        # If we can't parse the version, assume it's not modern
        return False


async def user_has_unlimited_power(client, user_id: str, room_id: str) -> bool:
    """Check if a user has unlimited power in a room (creator in modern room versions).

    Args:
        client: Matrix client instance
        user_id: The user ID to check
        room_id: The room ID to check in

    Returns:
        bool: True if user has unlimited power
    """
    try:
        room_version, creators = await get_room_version_and_creators(
            client, room_id, None
        )

        # In modern room versions (12+), creators have unlimited power
        if is_modern_room_version(room_version):
            return user_id in creators

        # In older room versions, creators don't have special unlimited power
        return False

    except Exception:
        return False


async def get_moderators_and_above(client, parent_room: str) -> List[str]:
    """Get list of users with moderator or higher permissions from the parent space.

    Args:
        client: Matrix client instance
        parent_room: The parent room ID

    Returns:
        list: List of user IDs with power level >= 50 (moderator or above)
    """
    try:
        power_levels = await client.get_state_event(
            parent_room, EventType.ROOM_POWER_LEVELS
        )
        moderators = []
        for user, level in power_levels.users.items():
            if level >= 50:  # Moderator level or above
                moderators.append(user)
        return moderators
    except Exception:
        return []
