"""User management utility functions."""

import fnmatch
import re
import asyncio
import time
from typing import List, Dict, Tuple
from mautrix.types import EventType, Membership, UserID
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


async def resolve_banlists(
    client, banlists: List[str], logger, failures: List[str] = None
) -> Dict[str, str]:
    """Resolve configured banlists to room IDs.

    Args:
        client: Matrix client instance
        banlists: List of banlist room IDs or aliases
        logger: Logger instance for error reporting
        failures: Optional list that unresolvable banlist aliases are appended to

    Returns:
        dict: Mapping of banlist room ID -> configured label (alias or ID).
            Banlists whose alias failed to resolve are omitted.
    """
    resolved = {}
    for banlist in banlists:
        if banlist.startswith("#"):
            try:
                room_info = await client.resolve_room_alias(banlist)
                resolved[room_info["room_id"]] = banlist
            except Exception as e:
                logger.error(f"Banlist fetching failed for {banlist}: {e!r}")
                if failures is not None:
                    failures.append(banlist)
                continue
        else:
            resolved[banlist] = banlist

    return resolved


async def get_banlist_roomids(client, banlists: List[str], logger) -> List[str]:
    """Get room IDs for all configured banlists.

    Args:
        client: Matrix client instance
        banlists: List of banlist room IDs or aliases
        logger: Logger instance for error reporting

    Returns:
        list: List of room IDs for banlists
    """
    return list((await resolve_banlists(client, banlists, logger)).keys())


# Memberships that mean a user is actually present in (or actively trying to
# get into) a room. Users who have left are not banned proactively; the banlist
# check on join catches them if they come back.
ACTIVE_MEMBERSHIPS = (Membership.JOIN, Membership.INVITE, Membership.KNOCK)


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
    members_only: bool = False,
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
        members_only: Only ban where the user is joined, invited or knocking
            (ignored when all_rooms is set)

    Returns:
        dict: Ban results with success/error lists. error_list maps the user
            to the rooms where the ban call itself failed; rooms whose
            membership could not be checked are skipped, not counted as errors.
    """
    ban_event_map = {"ban_list": {}, "error_list": {}}
    ban_event_map["ban_list"][user] = []

    for room in roomlist:
        if not all_rooms:
            try:
                member = await client.get_state_event(
                    room, EventType.ROOM_MEMBER, user
                )
            except MNotFound:
                # Never a member: nothing to ban and no messages to redact.
                continue
            except Exception as e:
                # Can't see the room (bot not joined, timeout, ...). For
                # proactive bans, skip it rather than reporting a ban failure
                # for a user we couldn't even look up; otherwise attempt the
                # ban anyway so a transient error doesn't let them through.
                if members_only:
                    if logger:
                        logger.warning(
                            f"Could not check membership of {user} in {room}, skipping: {e!r}"
                        )
                    continue
                if logger:
                    logger.warning(
                        f"Could not check membership of {user} in {room}, banning anyway: {e!r}"
                    )
            else:
                if (
                    members_only
                    and getattr(member, "membership", None) not in ACTIVE_MEMBERSHIPS
                ):
                    continue

        roomname = None
        try:
            roomnamestate = await client.get_state_event(room, "m.room.name")
            roomname = roomnamestate["name"]
        except Exception:
            pass

        try:
            await client.ban_user(room, user, reason=reason)
            ban_event_map["ban_list"][user].append(roomname or room)
            await asyncio.sleep(sleep_time)
        except Exception as e:
            if logger:
                logger.warning(f"Failed to ban {user} in {roomname or room}: {e!r}")
            ban_event_map["error_list"].setdefault(user, []).append(roomname or room)

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
