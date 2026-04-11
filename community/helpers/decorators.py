"""Decorators for common bot operations."""

import functools
from typing import Callable, Any, Optional
from mautrix.types import UserID, MessageEvent


def require_permission(min_level: int = 50, room_id: Optional[str] = None):
    """Decorator to require user permission for command execution.

    Args:
        min_level: Minimum required power level (default 50 for moderator)
        room_id: Room ID to check permissions in (None for parent room)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self, evt: MessageEvent, *args, **kwargs) -> Any:
            if not await self.user_permitted(evt.sender, min_level, room_id):
                await evt.reply("You don't have permission to use this command")
                return
            return await func(self, evt, *args, **kwargs)

        return wrapper

    return decorator


def require_parent_room(func: Callable) -> Callable:
    """Decorator to require parent room to be configured."""

    @functools.wraps(func)
    async def wrapper(self, evt: MessageEvent, *args, **kwargs) -> Any:
        if not await self.check_parent_room(evt):
            return
        return await func(self, evt, *args, **kwargs)

    return wrapper


def handle_errors(error_message: str = "An error occurred"):
    """Decorator to handle common errors in command execution.

    Args:
        error_message: Default error message to show to user
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self, evt: MessageEvent, *args, **kwargs) -> Any:
            try:
                return await func(self, evt, *args, **kwargs)
            except Exception as e:
                self.log.error(f"Error in {func.__name__}: {e}")
                await evt.reply(f"{error_message}: {e}")

        return wrapper

    return decorator
