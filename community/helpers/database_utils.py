"""Database utility functions."""

import asyncio
import time
from typing import List, Dict, Any
from mautrix.types import PaginationDirection


async def get_messages_to_redact(client, room_id: str, mxid: str, logger) -> List:
    """Get messages from a user in a room that should be redacted.

    Args:
        client: Matrix client instance
        room_id: The room ID to search in
        mxid: The user ID whose messages to find
        logger: Logger instance for error reporting

    Returns:
        list: List of message events to redact
    """
    try:
        messages = await client.get_messages(
            room_id,
            limit=100,
            filter_json={"senders": [mxid], "not_types": ["m.room.redaction"]},
            direction=PaginationDirection.BACKWARD,
        )
        # Filter out events with empty content
        filtered_events = [
            event
            for event in messages.events
            if event.content and event.content.serialize()
        ]
        logger.debug(
            f"DEBUG found {len(filtered_events)} messages to redact in {room_id} (after filtering empty content)"
        )
        return filtered_events
    except Exception as e:
        logger.error(f"Error getting messages to redact: {e}")
        return []


async def redact_messages(
    client, database, room_id: str, sleep_time: float, logger
) -> Dict[str, int]:
    """Redact messages queued for redaction in a room.

    Args:
        client: Matrix client instance
        database: Database instance
        room_id: The room ID to redact messages in
        sleep_time: Sleep time between redactions
        logger: Logger instance for error reporting

    Returns:
        dict: Counters for successful and failed redactions
    """
    counters = {"success": 0, "failure": 0}
    events = await database.fetch(
        "SELECT event_id FROM redaction_tasks WHERE room_id = $1", room_id
    )
    for event in events:
        try:
            await client.redact(room_id, event["event_id"], reason="content removed")
            counters["success"] += 1
            await database.execute(
                "DELETE FROM redaction_tasks WHERE event_id = $1", event["event_id"]
            )
            await asyncio.sleep(sleep_time)
        except Exception as e:
            if "Too Many Requests" in str(e):
                logger.warning(
                    f"Rate limited while redacting messages in {room_id}, will try again in next loop"
                )
                return counters
            logger.error(f"Failed to redact message: {e}")
            counters["failure"] += 1
            await asyncio.sleep(sleep_time)
    return counters


async def upsert_user_timestamp(database, mxid: str, timestamp: int, logger) -> None:
    """Insert or update user activity timestamp.

    Args:
        database: Database instance
        mxid: User Matrix ID
        timestamp: Activity timestamp
        logger: Logger instance for error reporting
    """
    try:
        await database.execute(
            """
            INSERT INTO user_events (mxid, last_message_timestamp, ignore_inactivity)
            VALUES ($1, $2, 0)
            ON CONFLICT (mxid) DO UPDATE SET
                last_message_timestamp = EXCLUDED.last_message_timestamp
            """,
            mxid,
            timestamp,
        )
    except Exception as e:
        logger.error(f"Failed to upsert user timestamp: {e}")


async def get_inactive_users(
    database, warn_threshold_days: int, kick_threshold_days: int, logger
) -> Dict[str, List[str]]:
    """Get lists of users who should be warned or kicked for inactivity.

    Args:
        database: Database instance
        warn_threshold_days: Days threshold for warning
        kick_threshold_days: Days threshold for kicking
        logger: Logger instance for error reporting

    Returns:
        dict: Contains 'warn' and 'kick' lists of user IDs
    """
    try:
        current_time = int(time.time())
        warn_threshold = current_time - (warn_threshold_days * 24 * 60 * 60)
        kick_threshold = current_time - (kick_threshold_days * 24 * 60 * 60)

        # Get users to warn
        warn_results = await database.fetch(
            """
            SELECT mxid FROM user_events 
            WHERE last_message_timestamp < $1 
            AND last_message_timestamp > $2
            AND ignore_inactivity = 0
            """,
            warn_threshold,
            kick_threshold,
        )

        # Get users to kick
        kick_results = await database.fetch(
            """
            SELECT mxid FROM user_events 
            WHERE last_message_timestamp < $2
            AND ignore_inactivity = 0
            """,
            kick_threshold,
        )

        return {
            "warn": [row["mxid"] for row in warn_results],
            "kick": [row["mxid"] for row in kick_results],
        }
    except Exception as e:
        logger.error(f"Failed to get inactive users: {e}")
        return {"warn": [], "kick": []}


async def cleanup_stale_verification_states(database, logger) -> None:
    """Clean up stale verification states older than 24 hours.

    Args:
        database: Database instance
        logger: Logger instance for error reporting
    """
    try:
        await database.execute(
            """
            DELETE FROM verification_states 
            WHERE created_at < NOW() - INTERVAL '24 hours'
            """
        )
    except Exception as e:
        logger.error(f"Failed to cleanup stale verification states: {e}")


async def get_verification_state(database, dm_room_id: str) -> Dict[str, Any]:
    """Get verification state for a DM room.

    Args:
        database: Database instance
        dm_room_id: The DM room ID

    Returns:
        dict: Verification state data or None if not found
    """
    try:
        result = await database.fetchrow(
            "SELECT * FROM verification_states WHERE dm_room_id = $1", dm_room_id
        )
        return dict(result) if result else None
    except Exception as e:
        return None


async def create_verification_state(
    database,
    dm_room_id: str,
    user_id: str,
    target_room_id: str,
    verification_phrase: str,
    attempts_remaining: int,
    required_power_level: int,
) -> None:
    """Create a new verification state.

    Args:
        database: Database instance
        dm_room_id: The DM room ID
        user_id: The user ID being verified
        target_room_id: The target room ID
        verification_phrase: The phrase to verify
        attempts_remaining: Number of attempts remaining
        required_power_level: Required power level for the target room
    """
    try:
        await database.execute(
            """
            INSERT INTO verification_states 
            (dm_room_id, user_id, target_room_id, verification_phrase, attempts_remaining, required_power_level)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            dm_room_id,
            user_id,
            target_room_id,
            verification_phrase,
            attempts_remaining,
            required_power_level,
        )
    except Exception as e:
        pass  # Verification state creation is not critical


async def update_verification_attempts(
    database, dm_room_id: str, attempts_remaining: int
) -> None:
    """Update verification attempts remaining.

    Args:
        database: Database instance
        dm_room_id: The DM room ID
        attempts_remaining: New number of attempts remaining
    """
    try:
        await database.execute(
            "UPDATE verification_states SET attempts_remaining = $1 WHERE dm_room_id = $2",
            attempts_remaining,
            dm_room_id,
        )
    except Exception as e:
        pass  # Verification state update is not critical


async def delete_verification_state(database, dm_room_id: str) -> None:
    """Delete a verification state.

    Args:
        database: Database instance
        dm_room_id: The DM room ID
    """
    try:
        await database.execute(
            "DELETE FROM verification_states WHERE dm_room_id = $1", dm_room_id
        )
    except Exception as e:
        pass  # Verification state deletion is not critical
