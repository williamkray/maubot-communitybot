"""Diagnostic utility functions for the community bot."""

from typing import Dict, List, Any, Tuple
from mautrix.types import EventType
from mautrix.client import Client


async def check_space_permissions(
    client: Client, parent_room: str, logger
) -> Dict[str, Any]:
    """Check bot permissions in the parent space.

    Args:
        client: Matrix client
        parent_room: Parent room ID
        logger: Logger instance

    Returns:
        Dict containing space permission information
    """
    try:
        space_power_levels = await client.get_state_event(
            parent_room, EventType.ROOM_POWER_LEVELS
        )
        bot_level = space_power_levels.get_user_level(client.mxid)

        # Check if bot has unlimited power (creator in modern room versions)
        from .room_utils import user_has_unlimited_power

        bot_has_unlimited_power = await user_has_unlimited_power(
            client, client.mxid, parent_room
        )

        space_info = {
            "room_id": parent_room,
            "bot_power_level": bot_level,
            "has_admin": bot_level >= 100 or bot_has_unlimited_power,
            "bot_has_unlimited_power": bot_has_unlimited_power,
            "users_higher_or_equal": [],
            "users_equal": [],
            "users_higher": [],
        }

        # Check for users with equal or higher power level
        for user, level in space_power_levels.users.items():
            if user != client.mxid and level >= bot_level:
                if level == bot_level:
                    space_info["users_equal"].append({"user": user, "level": level})
                else:
                    space_info["users_higher"].append({"user": user, "level": level})
                space_info["users_higher_or_equal"].append(
                    {"user": user, "level": level}
                )

        return space_info
    except Exception as e:
        logger.error(f"Failed to check space permissions: {e}")
        return {"room_id": parent_room, "error": str(e)}


async def check_room_permissions(
    client: Client, room_id: str, logger
) -> Dict[str, Any]:
    """Check bot permissions in a specific room.

    Args:
        client: Matrix client
        room_id: Room ID to check
        logger: Logger instance

    Returns:
        Dict containing room permission information
    """
    try:
        # Check if bot is in the room
        try:
            await client.get_state_event(room_id, EventType.ROOM_MEMBER, client.mxid)
        except:
            return {"room_id": room_id, "error": "Bot not in room"}

        # Get power levels
        room_power_levels = await client.get_state_event(
            room_id, EventType.ROOM_POWER_LEVELS
        )
        bot_level = room_power_levels.get_user_level(client.mxid)

        # Get room name if available
        room_name = room_id
        try:
            from .common_utils import get_room_name

            room_name = await get_room_name(client, room_id, logger) or room_id
        except:
            pass

        # Get room version and creators
        from .room_utils import get_room_version_and_creators

        room_version, creators = await get_room_version_and_creators(
            client, room_id, logger
        )

        # Check if bot has unlimited power (creator in modern room versions)
        from .room_utils import user_has_unlimited_power

        bot_has_unlimited_power = await user_has_unlimited_power(
            client, client.mxid, room_id
        )

        room_report = {
            "room_id": room_id,
            "room_name": room_name,
            "room_version": room_version,
            "creators": creators,
            "bot_power_level": bot_level,
            "has_admin": bot_level >= 100 or bot_has_unlimited_power,
            "bot_has_unlimited_power": bot_has_unlimited_power,
            "users_higher_or_equal": [],
            "users_equal": [],
            "users_higher": [],
        }

        # Check for users with equal or higher power level
        for user, level in room_power_levels.users.items():
            if user != client.mxid and level >= bot_level:
                if level == bot_level:
                    room_report["users_equal"].append({"user": user, "level": level})
                else:
                    room_report["users_higher"].append({"user": user, "level": level})
                room_report["users_higher_or_equal"].append(
                    {"user": user, "level": level}
                )

        return room_report
    except Exception as e:
        logger.error(f"Failed to check room permissions for {room_id}: {e}")
        return {"room_id": room_id, "error": str(e)}


def analyze_room_data(
    room_data: Dict[str, Any], is_modern_room_version_func
) -> Tuple[str, str, bool, bool, bool]:
    """Analyze room data to determine status and categorization.

    Args:
        room_data: Room data dictionary
        is_modern_room_version_func: Function to check if room version is modern

    Returns:
        Tuple of (status, category, is_admin, is_modern, has_error)
    """
    if "error" in room_data:
        if room_data["error"] == "Bot not in room":
            return "not_in_room", "error", False, False, True
        else:
            return "error", "error", False, False, True

    # Check if modern room version
    is_modern = is_modern_room_version_func(room_data.get("room_version", "1"))

    # Check admin status
    is_admin = room_data.get("has_admin", False)

    if is_admin:
        return "admin", "admin", True, is_modern, False
    else:
        return "no_admin", "problematic", False, is_modern, False


def generate_space_summary(space_data: Dict[str, Any]) -> str:
    """Generate HTML summary for space permissions.

    Args:
        space_data: Space permission data

    Returns:
        str: HTML formatted space summary
    """
    if "error" in space_data:
        return f"<h4>📋 Parent Space</h4><br />❌ <b>Error:</b> {space_data['error']}<br /><br />"

    space_status = "✅" if space_data.get("has_admin", False) else "❌"
    response = "<h4>📋 Parent Space</h4><br />"

    # Show admin status with appropriate details
    if space_data.get("bot_has_unlimited_power", False):
        response += f"{space_status} <b>Administrative privileges:</b> Yes (unlimited power - creator)<br />"
    else:
        response += f"{space_status} <b>Administrative privileges:</b> {'Yes' if space_data['has_admin'] else 'No'} (level: {space_data['bot_power_level']})<br />"

    if space_data.get("users_higher"):
        response += f"⚠️ <b>Users with higher power:</b> {', '.join([f'{u['user']} ({u['level']})' for u in space_data['users_higher']])}<br />"
    if space_data.get("users_equal"):
        response += f"⚠️ <b>Users with equal power:</b> {', '.join([f'{u['user']} ({u['level']})' for u in space_data['users_equal']])}<br />"

    response += "<br />"
    return response


def generate_room_summary(
    rooms_data: Dict[str, Any], is_modern_room_version_func
) -> Tuple[str, Dict[str, int]]:
    """Generate HTML summary for room permissions.

    Args:
        rooms_data: Dictionary of room data
        is_modern_room_version_func: Function to check if room version is modern

    Returns:
        Tuple of (HTML response, statistics dict)
    """
    problematic_rooms = []
    stats = {
        "admin_rooms": 0,
        "non_admin_rooms": 0,
        "error_rooms": 0,
        "not_in_room_count": 0,
        "modern_rooms": 0,
        "legacy_rooms": 0,
    }

    for room_id, room_data in rooms_data.items():
        status, category, is_admin, is_modern, has_error = analyze_room_data(
            room_data, is_modern_room_version_func
        )

        # Update statistics
        if has_error:
            stats["error_rooms"] += 1
            if room_data.get("error") == "Bot not in room":
                stats["not_in_room_count"] += 1
        else:
            if is_admin:
                stats["admin_rooms"] += 1
            else:
                stats["non_admin_rooms"] += 1

            if is_modern:
                stats["modern_rooms"] += 1
            else:
                stats["legacy_rooms"] += 1

        # Generate room info for problematic rooms
        if category in ["error", "problematic"] or (
            is_admin and (room_data.get("users_higher") or room_data.get("users_equal"))
        ):
            if has_error:
                if room_data["error"] == "Bot not in room":
                    problematic_rooms.append(
                        f"❌ <b>{room_data.get('room_name', room_id)}</b> ({room_id}): Bot not in room"
                    )
                else:
                    problematic_rooms.append(
                        f"❌ <b>{room_data.get('room_name', room_id)}</b> ({room_id}): Error - {room_data['error']}"
                    )
            elif is_admin:
                # Show unlimited power status for modern rooms
                if room_data.get("bot_has_unlimited_power", False):
                    room_info = f"✅ <b>{room_data['room_name']}</b> ({room_id}): Unlimited Power (Creator) [v{room_data.get('room_version', '1')}]"
                else:
                    room_info = f"✅ <b>{room_data['room_name']}</b> ({room_id}): Admin: Yes (level: {room_data['bot_power_level']}) [v{room_data.get('room_version', '1')}]"

                # Add power level conflict info
                if room_data.get("users_higher") or room_data.get("users_equal"):
                    if room_data.get("bot_has_unlimited_power", False):
                        room_info += " - Note: Power level conflicts are irrelevant for creators with unlimited power"
                    else:
                        if room_data.get("users_higher"):
                            room_info += f" - Higher power users: {len(room_data['users_higher'])}"
                        if room_data.get("users_equal"):
                            room_info += (
                                f" - Equal power users: {len(room_data['users_equal'])}"
                            )
                problematic_rooms.append(room_info)
            else:
                problematic_rooms.append(
                    f"❌ <b>{room_data['room_name']}</b> ({room_id}): Admin: No (level: {room_data['bot_power_level']}) [v{room_data.get('room_version', '1')}]"
                )

    # Generate HTML response
    response = ""
    if problematic_rooms:
        response += f"<h4>🏠 Problematic Rooms ({len(problematic_rooms)} of {len(rooms_data)} total)</h4><br />"
        response += "<i>Use <code>!community doctor &lt;room_id&gt;</code> for detailed analysis of specific rooms</i><br /><br />"
        for room_info in problematic_rooms:
            response += f"{room_info}<br />"
        response += "<br />"

    return response, stats


def generate_summary_stats(
    space_data: Dict[str, Any], room_stats: Dict[str, int]
) -> str:
    """Generate summary statistics HTML.

    Args:
        space_data: Space permission data
        room_stats: Room statistics

    Returns:
        str: HTML formatted summary statistics
    """
    response = "<h4>📊 Summary</h4><br />"
    response += f"• Parent space: {'✅ Admin' if space_data.get('has_admin', False) else '❌ No admin'}<br />"
    response += f"• Rooms with admin: {room_stats['admin_rooms']}<br />"
    response += f"• Rooms without admin: {room_stats['non_admin_rooms']}<br />"
    response += f"• Modern room versions (12+): {room_stats['modern_rooms']}<br />"
    response += f"• Legacy room versions (1-11): {room_stats['legacy_rooms']}<br />"

    # Add note about unlimited power for modern rooms
    if room_stats["modern_rooms"] > 0:
        response += "<br />ℹ️ <b>Note:</b> In modern room versions (12+), creators have unlimited power and cannot be restricted by power levels.<br />"

    if room_stats["not_in_room_count"] > 0:
        response += f"• Rooms bot not in: {room_stats['not_in_room_count']}<br />"
    if room_stats["error_rooms"] > 0:
        response += f"• Rooms with errors: {room_stats['error_rooms']}<br />"

    response += "<br />"
    return response


def generate_issues_and_warnings(issues: List[str], warnings: List[str]) -> str:
    """Generate issues and warnings HTML.

    Args:
        issues: List of critical issues
        warnings: List of warnings

    Returns:
        str: HTML formatted issues and warnings
    """
    response = ""

    if issues:
        response += "<h4>🚨 Critical Issues</h4><br />"
        for issue in issues:
            response += f"• {issue}<br />"
        response += "<br />"

    if warnings:
        response += "<h4>⚠️ Warnings</h4><br />"
        for warning in warnings:
            response += f"• {warning}<br />"
        response += "<br />"

    return response


def generate_all_clear_message() -> str:
    """Generate all clear message HTML.

    Returns:
        str: HTML formatted all clear message
    """
    return "<h4>✅ All Clear</h4><br />No permission issues detected. The bot should be able to manage all rooms and users effectively.<br />"
