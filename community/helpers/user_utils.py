"""User management utility functions."""

import fnmatch
import re
import time
from typing import List, Dict, Tuple
from mautrix.types import EventType, UserID
from mautrix.errors import MNotFound


async def check_if_banned(client, userid: str, banlists: List[str], logger) -> bool:
    """Check if a user is banned according to banlists.

    Args:
        client: Matrix client instance
        userid: The user ID to check
        banlists: List of banlist room IDs or aliases
        logger: Logger instance for error reporting

    Returns:
        bool: True if user is banned
    """
    is_banned = False
    myrooms = await client.get_joined_rooms()
    banlist_roomids = await get_banlist_roomids(client, banlists, logger)

    for list_id in banlist_roomids:
        if list_id not in myrooms:
            logger.error(
                f"Bot must be in {list_id} before attempting to use it as a banlist."
            )
            continue

        try:
            list_state = await client.get_state(list_id)
            user_policies = list(
                filter(lambda p: p.type.t == "m.policy.rule.user", list_state)
            )
        except Exception as e:
            logger.error(e)
            continue

        for rule in user_policies:
            try:
                if bool(fnmatch.fnmatch(userid, rule["content"]["entity"])) and bool(
                    re.search("ban$", rule["content"]["recommendation"])
                ):
                    return True
            except Exception:
                # Skip invalid rules
                pass

    return is_banned


async def get_banlist_roomids(client, banlists: List[str], logger) -> List[str]:
    """Get room IDs for all configured banlists.

    Args:
        client: Matrix client instance
        banlists: List of banlist room IDs or aliases
        logger: Logger instance for error reporting

    Returns:
        list: List of room IDs for banlists
    """
    banlist_roomids = []
    for banlist in banlists:
        if banlist.startswith("#"):
            try:
                room_info = await client.resolve_room_alias(banlist)
                list_id = room_info["room_id"]
                banlist_roomids.append(list_id)
            except Exception as e:
                logger.error(f"Banlist fetching failed for {banlist}: {e}")
                continue
        else:
            list_id = banlist
            banlist_roomids.append(list_id)

    return banlist_roomids


async def ban_user_from_rooms(
    client,
    user: str,
    roomlist: List[str],
    reason: str = "banned",
    all_rooms: bool = False,
    redact_on_ban: bool = False,
    get_messages_to_redact_func=None,
    database=None,
    sleep_time: float = 0.1,
    logger=None,
) -> Dict:
    """Ban a user from a list of rooms.

    Args:
        client: Matrix client instance
        user: User ID to ban
        roomlist: List of room IDs to ban from
        reason: Reason for the ban
        all_rooms: Whether to ban even if user is not in room
        redact_on_ban: Whether to queue messages for redaction
        get_messages_to_redact_func: Function to get messages to redact
        database: Database instance for redaction tasks
        sleep_time: Sleep time between operations
        logger: Logger instance

    Returns:
        dict: Ban results with success/error lists
    """
    ban_event_map = {"ban_list": {}, "error_list": {}}
    ban_event_map["ban_list"][user] = []

    for room in roomlist:
        try:
            roomname = None
            try:
                roomnamestate = await client.get_state_event(room, "m.room.name")
                roomname = roomnamestate["name"]
            except:
                pass

            # ban user even if they're not in the room!
            if not all_rooms:
                await client.get_state_event(room, EventType.ROOM_MEMBER, user)

            await client.ban_user(room, user, reason=reason)
            if roomname:
                ban_event_map["ban_list"][user].append(roomname)
            else:
                ban_event_map["ban_list"][user].append(room)
            time.sleep(sleep_time)
        except MNotFound:
            pass
        except Exception as e:
            if logger:
                logger.warning(e)
            ban_event_map["error_list"][user] = []
            ban_event_map["error_list"][user].append(roomname or room)

        if redact_on_ban and get_messages_to_redact_func and database:
            messages = await get_messages_to_redact_func(room, user)
            # Queue messages for redaction
            for msg in messages:
                await database.execute(
                    "INSERT INTO redaction_tasks (event_id, room_id) VALUES ($1, $2)",
                    msg.event_id,
                    room,
                )
            if logger:
                logger.info(
                    f"Queued {len(messages)} messages for redaction in {roomname or room}"
                )

    return ban_event_map


async def user_permitted(
    client,
    user_id: UserID,
    parent_room: str,
    min_level: int = 50,
    room_id: str = None,
    logger=None,
) -> bool:
    """Check if a user has sufficient power level in a room.

    Args:
        client: Matrix client instance
        user_id: The Matrix ID of the user to check
        parent_room: The parent room ID
        min_level: Minimum required power level (default 50 for moderator)
        room_id: The room ID to check permissions in. If None, uses parent room.
        logger: Logger instance for error reporting

    Returns:
        bool: True if user has sufficient power level
    """
    try:
        target_room = room_id or parent_room

        # First check if user has unlimited power (creator in modern room versions)
        from .room_utils import user_has_unlimited_power

        if await user_has_unlimited_power(client, user_id, target_room):
            return True

        # Then check power level
        power_levels = await client.get_state_event(
            target_room, EventType.ROOM_POWER_LEVELS
        )
        user_level = power_levels.get_user_level(user_id)
        return user_level >= min_level
    except Exception as e:
        if logger:
            logger.error(f"Failed to check user power level: {e}")
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
    from .room_utils import user_has_unlimited_power as room_user_has_unlimited_power

    return await room_user_has_unlimited_power(client, user_id, room_id)
