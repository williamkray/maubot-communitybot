"""Room creation utility functions for the community bot."""

import re
import asyncio
from typing import Optional, Tuple, List, Dict, Any
from mautrix.types import MessageEvent, PowerLevelStateEventContent, EventType
from mautrix.client import Client

# --encrypted / --unencrypted flags (at beginning, middle, or end of a string)
ENCRYPTED_FLAG_REGEX = re.compile(r"(\s+|^)-+encrypt(ed)?(\s+|$)")
UNENCRYPTED_FLAG_REGEX = re.compile(r"(\s+|^)-+unencrypt(ed)?(\s+|$)")
# --voice flag: create a MatrixRTC call room instead of a text room
VOICE_FLAG_REGEX = re.compile(r"(\s+|^)-+voice(\s+|$)")

# MSC3417 call-room type. Clients that support voice rooms (Cinny, Sable,
# Element Web) render rooms with this m.room.create type call-first; others
# show them as ordinary rooms.
CALL_ROOM_TYPE = "org.matrix.msc3417.call"
# Legacy (MSC3401) call state events: per-device membership state, plus the
# placeholder "call" event Cinny/Sable put in new call rooms.
CALL_MEMBER_EVENT = "org.matrix.msc3401.call.member"
CALL_EVENT = "org.matrix.msc3401.call"
# MSC4143 (Matrix 2.0) RTC events. A slot is a state event an admin opens so
# members can join a call by sending rtc.member sticky (non-state) events.
RTC_SLOT_EVENTS = ("org.matrix.msc4143.rtc.slot", "m.rtc.slot")
RTC_MEMBER_EVENTS = ("org.matrix.msc4143.rtc.member", "m.rtc.member")
# The default call slot, as (event type, state key, per-member encryption
# type) for each wire format. Deployed clients (Element Call, matrix-js-sdk)
# use the unstable form; clients on homeservers advertising
# org.matrix.msc4143.stable (e.g. Sable) only accept the stable one.
RTC_CALL_SLOTS = (
    ("org.matrix.msc4143.rtc.slot", "m.call#ROOM", "org.matrix.msc4143.per_member"),
    ("m.rtc.slot", "m.call#room", "m.per_member"),
)

# Synapse's default per-event power levels. power_level_content_override
# replaces the whole "events" map, so these must be restated whenever we send
# one, or they silently fall back to state_default (50).
DEFAULT_EVENT_POWER_LEVELS = {
    "m.room.avatar": 50,
    "m.room.canonical_alias": 50,
    "m.room.encryption": 100,
    "m.room.history_visibility": 100,
    "m.room.name": 50,
    "m.room.power_levels": 100,
    "m.room.server_acl": 100,
    "m.room.tombstone": 100,
}


def extract_encryption_flags(text: str) -> Tuple[str, bool, bool]:
    """Strip --encrypted / --unencrypted flags from a command argument.

    Returns:
        Tuple of (cleaned_text, force_encryption, force_unencryption)
    """
    force_encryption = bool(ENCRYPTED_FLAG_REGEX.search(text))
    force_unencryption = bool(UNENCRYPTED_FLAG_REGEX.search(text))
    if force_encryption:
        text = ENCRYPTED_FLAG_REGEX.sub(" ", text)
    if force_unencryption:
        text = UNENCRYPTED_FLAG_REGEX.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(), force_encryption, force_unencryption


def extract_voice_flag(text: str) -> Tuple[str, bool]:
    """Strip a --voice flag from a command argument.

    Returns:
        Tuple of (cleaned_text, voice)
    """
    voice = bool(VOICE_FLAG_REGEX.search(text))
    if voice:
        text = VOICE_FLAG_REGEX.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(), voice


def is_call_room_type(room_type: Any) -> bool:
    """True if an m.room.create ``type`` (RoomType enum or str) marks a call room."""
    return getattr(room_type, "value", room_type) == CALL_ROOM_TYPE


def call_event_power_levels(voice: bool, call_power_level: int = 50) -> Dict[str, int]:
    """Build the ``events`` power-level map for a new room.

    Restates the server defaults, then sets the call-related event levels:

    - voice rooms: anyone may join a call. The legacy call.member state event
      is both "start" and "join", so it must be 0; rtc.member (Matrix 2.0) is
      0 too. The slot is pre-opened at creation (see ``voice_room_initial_state``),
      so only closing/reconfiguring it needs moderator power.
    - text rooms: ``call_power_level`` gates starting calls. In the legacy
      model it also gates joining (start and join are the same event); with
      Matrix 2.0 slots, a moderator opens the slot and members join freely.
    """
    events = dict(DEFAULT_EVENT_POWER_LEVELS)
    if voice:
        events[CALL_MEMBER_EVENT] = 0
        for slot_event in RTC_SLOT_EVENTS:
            events[slot_event] = 50
    else:
        events[CALL_MEMBER_EVENT] = call_power_level
        events[CALL_EVENT] = call_power_level
        for slot_event in RTC_SLOT_EVENTS:
            events[slot_event] = call_power_level
    for member_event in RTC_MEMBER_EVENTS:
        events[member_event] = 0
    return events


def voice_room_creation_content(
    creation_content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return ``creation_content`` (copied, may be None) with the call-room type set."""
    content = dict(creation_content or {})
    content["type"] = CALL_ROOM_TYPE
    return content


def voice_room_initial_state(encrypted: bool) -> List[Dict[str, Any]]:
    """Initial state that makes a room usable as a voice room in every model.

    - the empty legacy ``call`` event Cinny/Sable create alongside call rooms
    - an open MSC4143 call slot in both the unstable and stable forms, so
      members can join Matrix 2.0 calls without needing permission to open
      one. Encrypted rooms use per-member keys.
    """
    state: List[Dict[str, Any]] = [{"type": CALL_EVENT, "state_key": "", "content": {}}]
    for slot_event, slot_key, encryption_type in RTC_CALL_SLOTS:
        slot_content: Dict[str, Any] = {
            "status": "open",
            "application": {"type": "m.call"},
        }
        if encrypted:
            slot_content["encryption"] = {"type": encryption_type}
        state.append({"type": slot_event, "state_key": slot_key, "content": slot_content})
    return state


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
    roomname, force_encryption, force_unencryption = extract_encryption_flags(
        roomname
    )

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


def pick_placement_parent(old_parents: list, parent_room: str) -> str:
    """Choose which space a replacement room should be homed under.

    Prefers a subspace the old room lived in over the top-level community
    ``parent_room`` (so replacing a subspace room keeps it in that subspace),
    falling back to any found parent, then to ``parent_room``.
    """
    subspace_parents = [p for p in (old_parents or []) if p != parent_room]
    if subspace_parents:
        return subspace_parents[0]
    if old_parents:
        return old_parents[0]
    return parent_room


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
