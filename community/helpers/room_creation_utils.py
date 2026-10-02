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

    # Check if community slug is configured (only required when the slug suffix is used)
    if config.get("use_community_slug", True) and not config.get("community_slug", ""):
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
    # Create alias, optionally suffixed with the community slug
    if config.get("use_community_slug", True):
        alias_localpart = f"{sanitized_name}-{config.get('community_slug', '')}"
    else:
        alias_localpart = sanitized_name

    # Get server and invitees
    server = client.parse_user_id(client.mxid)[1]
    room_invitees = invitees if invitees is not None else config.get("invitees", [])
    parent_room = config.get("parent_room", "")

    return alias_localpart, server, room_invitees, parent_room


def merge_user_power_levels(base_users: dict, extra_users: dict) -> dict:
    """Merge two user -> power-level maps, keeping the HIGHER level for any user
    present in both. Returns a new dict (inputs are not mutated). Used so a room
    nested under a subspace gets both the community admins and any delegated
    subspace admins, without ever lowering a community admin's level."""
    merged = dict(base_users or {})
    for user_id, level in (extra_users or {}).items():
        merged[user_id] = max(level, merged.get(user_id, 0))
    return merged


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
                except Exception as e:
                    # If copying users fails, create default power levels
                    power_levels.users = {
                        client.mxid: 1000,  # Bot gets highest power
                    }
            else:
                power_levels.users = {
                    client.mxid: 1000,  # Bot gets highest power
                }

            # Set explicit config values
            power_levels.invite = config.get("invite_power_level", 50)

            return power_levels
        except Exception as e:
            # If we can't get parent power levels, create default ones
            power_levels = PowerLevelStateEventContent()
            power_levels.users = {
                client.mxid: 1000,  # Bot gets highest power
            }
            power_levels.invite = config.get("invite_power_level", 50)
            return power_levels
    else:
        # If no parent room, create default power levels
        power_levels = PowerLevelStateEventContent()
        power_levels.users = {
            client.mxid: 1000,  # Bot gets highest power
        }
        power_levels.invite = config.get("invite_power_level", 50)
        return power_levels


def restricted_join_rule_state(parent_room: str) -> Dict[str, Any]:
    """Build an m.room.join_rules state event using the "restricted" join rule,
    letting members of ``parent_room`` self-join without an explicit invite.
    Shared by managed room creation and subspace creation so both default to
    community-membership-based access."""
    return {
        "type": str(EventType.ROOM_JOIN_RULES),
        "content": {
            "join_rule": "restricted",
            "allow": [{"type": "m.room_membership", "room_id": parent_room}],
        },
    }


def extract_target_flag(text: str) -> Tuple[str, Optional[str]]:
    """Split an optional ``--under``/``--target <space>`` flag out of a raw
    command string (shared by ``room create`` and ``space create``).

    The flag value runs until the next ``--flag`` token or end of string, so it
    may be a multi-word subspace display name (e.g. "Sub V12"); any other flags
    such as ``--encrypted`` are left in place. Returns
    ``(text_without_the_under_flag, target_or_None)`` with whitespace collapsed.
    """
    m = re.search(r"(?:\s|^)-+(?:under|target)\s+(.+?)(?=\s+-+[a-zA-Z]|$)", text)
    if not m:
        return text, None
    target = m.group(1).strip()
    cleaned = re.sub(r"\s+", " ", (text[: m.start()] + " " + text[m.end() :])).strip()
    return cleaned, target


def prepare_initial_state(
    config: dict,
    parent_room: str,
    server: str,
    force_encryption: bool,
    force_unencryption: bool,
    creation_content: Optional[Dict[str, Any]] = None,
    join_rule_room: Optional[str] = None,
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
        # The room/subspace is linked under ``parent_room`` (its place in the
        # tree), but the restricted join rule can gate on a different room via
        # ``join_rule_room`` — used so a room nested under a subspace still lets
        # the whole community join. Defaults to ``parent_room``.
        join_gate = join_rule_room if join_rule_room else parent_room
        initial_state.extend(
            [
                {
                    "type": str(EventType.SPACE_PARENT),
                    "state_key": parent_room,
                    "content": {"via": [server], "canonical": True},
                },
                restricted_join_rule_state(join_gate),
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
    client: Client,
    parent_room: str,
    room_id: str,
    server: str,
    sleep_duration: float,
    log=None,
) -> None:
    """Add created room to parent space.

    Args:
        client: Matrix client
        parent_room: Parent room ID
        room_id: Created room ID
        server: Server name
        sleep_duration: Sleep duration between operations
        log: Accepted for backwards compatibility; unused (rate-limit retries
            are applied centrally by CommunityBot._install_rate_limit_retries).
    """
    if parent_room:
        # send_state_event is centrally wrapped with rate-limit retry.
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
