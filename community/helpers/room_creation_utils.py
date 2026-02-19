"""Room creation utility functions for the community bot."""

import re
import asyncio
from typing import Optional, Tuple, List, Dict, Any
from mautrix.types import MessageEvent, PowerLevelStateEventContent, EventType
from mautrix.client import Client


async def validate_room_creation_params(
    roomname: str, config: dict, evt: Optional[MessageEvent] = None
) -> Tuple[str, bool, bool, str]:
    """Validate and process room creation parameters.

    Args:
        roomname: Original room name
        config: Bot configuration
        evt: Optional MessageEvent for error responses

    Returns:
        Tuple of (sanitized_name, force_encryption, force_unencryption, error_msg)
    """
    # Check for encryption flags (at beginning, middle, or end of string)
    encrypted_flag_regex = re.compile(r"(\s+|^)-+encrypt(ed)?(\s+|$)")
    unencrypted_flag_regex = re.compile(r"(\s+|^)-+unencrypt(ed)?(\s+|$)")
    force_encryption = bool(encrypted_flag_regex.search(roomname))
    force_unencryption = bool(unencrypted_flag_regex.search(roomname))

    # Clean up room name
    if force_encryption:
        roomname = encrypted_flag_regex.sub("", roomname)  # Remove encryption flag
    if force_unencryption:
        roomname = unencrypted_flag_regex.sub("", roomname)  # Remove unencryption flag

    # Clean up any extra whitespace
    roomname = re.sub(r"\s+", " ", roomname).strip()

    sanitized_name = re.sub(r"[^a-zA-Z0-9]", "", roomname).lower()

    # Check if community slug is configured
    if not config.get("community_slug", ""):
        error_msg = "No community slug configured. Please run initialize command first."
        return sanitized_name, force_encryption, force_unencryption, error_msg, roomname

    return sanitized_name, force_encryption, force_unencryption, "", roomname


async def prepare_room_creation_data(
    sanitized_name: str,
    config: dict,
    client: Client,
    invitees: Optional[List[str]] = None,
) -> Tuple[str, str, List[str], str]:
    """Prepare data needed for room creation.

    Args:
        sanitized_name: Sanitized room name
        config: Bot configuration
        client: Matrix client
        invitees: Optional list of users to invite

    Returns:
        Tuple of (alias_localpart, server, room_invitees, parent_room)
    """
    # Create alias with community slug
    alias_localpart = f"{sanitized_name}-{config.get('community_slug', '')}"

    # Get server and invitees
    server = client.parse_user_id(client.mxid)[1]
    room_invitees = invitees if invitees is not None else config.get("invitees", [])
    parent_room = config.get("parent_room", "")

    return alias_localpart, server, room_invitees, parent_room


async def prepare_power_levels(
    client: Client,
    config: dict,
    parent_room: str,
    power_level_override: Optional[PowerLevelStateEventContent] = None,
) -> PowerLevelStateEventContent:
    """Prepare power levels for room creation.

    Args:
        client: Matrix client
        config: Bot configuration
        parent_room: Parent room ID
        power_level_override: Optional existing power level override

    Returns:
        PowerLevelStateEventContent for room creation
    """
    if power_level_override:
        return power_level_override

    if parent_room:
        try:
            # Get parent room power levels to extract user power levels
            parent_power_levels = await client.get_state_event(
                parent_room, EventType.ROOM_POWER_LEVELS
            )

            # Create new power levels with server defaults, not copying all permissions from space
            power_levels = PowerLevelStateEventContent()

            # Copy only user power levels from parent space, not the entire permission set
            if (
                parent_power_levels
                and hasattr(parent_power_levels, "users")
                and parent_power_levels.users
            ):
                try:
                    user_power_levels = parent_power_levels.users.copy()
                    # Ensure bot has highest power
                    user_power_levels[client.mxid] = 1000
                    power_levels.users = user_power_levels
                except Exception:
                    # If copying users fails, create default power levels
                    power_levels.users = {client.mxid: 1000}  # Bot gets highest power
            else:
                power_levels.users = {client.mxid: 1000}  # Bot gets highest power

            # Set explicit config values
            power_levels.invite = config.get("invite_power_level", 50)

            return power_levels
        except Exception:
            # If we can't get parent power levels, create default ones
            power_levels = PowerLevelStateEventContent()
            power_levels.users = {client.mxid: 1000}  # Bot gets highest power
            power_levels.invite = config.get("invite_power_level", 50)
            return power_levels
    else:
        # If no parent room, create default power levels
        power_levels = PowerLevelStateEventContent()
        power_levels.users = {client.mxid: 1000}  # Bot gets highest power
        power_levels.invite = config.get("invite_power_level", 50)
        return power_levels


def prepare_initial_state(
    config: dict,
    parent_room: str,
    server: str,
    force_encryption: bool,
    force_unencryption: bool,
    creation_content: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Prepare initial state events for room creation.

    Args:
        config: Bot configuration
        parent_room: Parent room ID
        server: Server name
        force_encryption: Whether to force encryption
        force_unencryption: Whether to force no encryption
        creation_content: Optional creation content

    Returns:
        List of initial state events
    """
    initial_state = []

    # Only add space parent state if we have a parent room
    if parent_room:
        initial_state.extend(
            [
                {
                    "type": str(EventType.SPACE_PARENT),
                    "state_key": parent_room,
                    "content": {"via": [server], "canonical": True},
                },
                {
                    "type": str(EventType.ROOM_JOIN_RULES),
                    "content": {
                        "join_rule": "restricted",
                        "allow": [
                            {"type": "m.room_membership", "room_id": parent_room}
                        ],
                    },
                },
            ]
        )

    # Add encryption if needed
    if (config.get("encrypt", False) and not force_unencryption) or force_encryption:
        initial_state.append(
            {
                "type": str(EventType.ROOM_ENCRYPTION),
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            }
        )

    # Add history visibility if specified in creation_content
    if creation_content and "m.room.history_visibility" in creation_content:
        initial_state.append(
            {
                "type": str(EventType.ROOM_HISTORY_VISIBILITY),
                "content": {
                    "history_visibility": creation_content.get(
                        "m.room.history_visibility", "joined"
                    )
                },
            }
        )

    return initial_state


def adjust_power_levels_for_modern_rooms(
    power_levels: PowerLevelStateEventContent, room_version: str
) -> PowerLevelStateEventContent:
    """Adjust power levels for modern room versions.

    Args:
        power_levels: Power level state content
        room_version: Room version string

    Returns:
        Adjusted power level state content
    """
    # For modern room versions (12+), remove the bot from power levels
    # as creators have unlimited power by default and cannot appear in power levels
    if room_version and int(room_version) >= 12 and power_levels:
        if power_levels.users:
            # Remove bot from users list but keep other important settings
            power_levels.users.pop(
                "bot_mxid", None
            )  # Will be replaced with actual bot mxid

    return power_levels


async def add_room_to_space(
    client: Client, parent_room: str, room_id: str, server: str, sleep_duration: float
) -> None:
    """Add created room to parent space.

    Args:
        client: Matrix client
        parent_room: Parent room ID
        room_id: Created room ID
        server: Server name
        sleep_duration: Sleep duration between operations
    """
    if parent_room:
        await client.send_state_event(
            parent_room,
            EventType.SPACE_CHILD,
            {"via": [server], "suggested": False},
            state_key=room_id,
        )
        await asyncio.sleep(sleep_duration)


async def verify_room_creation(
    client: Client, room_id: str, expected_version: str, logger
) -> None:
    """Verify that room was created with correct settings.

    Args:
        client: Matrix client
        room_id: Created room ID
        expected_version: Expected room version
        logger: Logger instance
    """
    try:
        from .room_utils import get_room_version_and_creators

        actual_version, actual_creators = await get_room_version_and_creators(
            client, room_id, logger
        )
        logger.info(
            f"Room {room_id} created with version {actual_version} (requested: {expected_version})"
        )
        if actual_version != expected_version:
            logger.warning(
                f"Room version mismatch: requested {expected_version}, got {actual_version}"
            )
    except Exception as e:
        logger.warning(f"Could not verify room version for {room_id}: {e}")
