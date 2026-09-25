"""Common utility functions for bot operations."""

import asyncio
import re
from typing import Optional, Dict, Any
from mautrix.types import EventType, MessageEvent
from mautrix.errors import MLimitExceeded

# Synapse embeds the requested delay as `retry_after_ms` in the 429 body, but
# the installed mautrix version discards it before raising MLimitExceeded, so it
# is only sometimes recoverable from the human-readable message.
_RETRY_AFTER_RE = re.compile(r"retry_after_ms['\"\s:=]+(\d+)")


def _extract_retry_after_seconds(exc) -> Optional[float]:
    """Recover a server-requested retry delay (in seconds) from a 429 if present.

    Returns None when the delay can't be found, in which case callers should
    fall back to their own backoff.
    """
    msg = getattr(exc, "message", "") or ""
    match = _RETRY_AFTER_RE.search(str(msg))
    if match:
        return int(match.group(1)) / 1000.0
    return None


async def with_rate_limit_retry(
    coro_factory,
    *,
    log,
    description: str = "request",
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    abort_over: float = 60.0,
):
    """Await ``coro_factory()``, retrying on homeserver rate limits (HTTP 429).

    The installed mautrix does not retry on 429, so this wraps a call and retries
    with the server-requested delay when available, otherwise a capped
    exponential backoff.

    Args:
        coro_factory: zero-arg callable returning a FRESH awaitable each call
            (a coroutine can only be awaited once, so pass e.g.
            ``lambda: self.client.create_room(...)``).
        log: logger for retry diagnostics.
        description: short label for log messages (e.g. "create room").
        max_retries: how many times to retry before giving up.
        base_delay/max_delay: bounds for the exponential backoff.
        abort_over: if a required wait exceeds this many seconds, give up and
            re-raise instead of blocking (some servers ask for minutes).

    Returns:
        Whatever the awaited call returns.

    Raises:
        MLimitExceeded: if retries are exhausted or the required wait is too long.
    """
    delay = base_delay
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except MLimitExceeded as e:
            server_wait = _extract_retry_after_seconds(e)
            wait = server_wait if server_wait is not None else delay
            if wait > abort_over or attempt == max_retries:
                log.warning(
                    f"rate limited on {description}; giving up after "
                    f"{attempt + 1} attempt(s) (would need to wait ~{wait:.0f}s)"
                )
                raise
            log.warning(
                f"rate limited on {description}; retry "
                f"{attempt + 1}/{max_retries} in {wait:.1f}s"
            )
            await asyncio.sleep(wait)
            delay = min(delay * 2, max_delay)


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
