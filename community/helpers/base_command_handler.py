"""Base command handler for common command patterns."""

from abc import ABC, abstractmethod
from typing import Any, Optional
from mautrix.types import MessageEvent, UserID
from .decorators import require_permission, require_parent_room, handle_errors


class BaseCommandHandler(ABC):
    """Base class for command handlers with common patterns."""
    
    def __init__(self, bot):
        """Initialize with bot instance.
        
        Args:
            bot: CommunityBot instance
        """
        self.bot = bot
        self.client = bot.client
        self.config = bot.config
        self.config_manager = bot.config_manager
        self.log = bot.log
        self.database = bot.database
    
    @abstractmethod
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass
    
    async def check_permissions(self, evt: MessageEvent, min_level: int = 50, room_id: str = None) -> bool:
        """Check if user has required permissions.
        
        Args:
            evt: Message event
            min_level: Minimum required power level
            room_id: Room ID to check permissions in
            
        Returns:
            bool: True if user has permissions
        """
        return await self.bot.user_permitted(evt.sender, min_level, room_id)
    
    async def check_parent_room(self, evt: MessageEvent) -> bool:
        """Check if parent room is configured.
        
        Args:
            evt: Message event
            
        Returns:
            bool: True if parent room is configured
        """
        return await self.bot.check_parent_room(evt)
    
    async def reply_error(self, evt: MessageEvent, message: str) -> None:
        """Reply with an error message.
        
        Args:
            evt: Message event
            message: Error message
        """
        await evt.reply(message)
    
    async def reply_success(self, evt: MessageEvent, message: str) -> None:
        """Reply with a success message.
        
        Args:
            evt: Message event
            message: Success message
        """
        await evt.reply(message)
    
    async def respond_html(self, evt: MessageEvent, message: str, edits: Optional[MessageEvent] = None) -> None:
        """Respond with HTML content.
        
        Args:
            evt: Message event
            message: HTML message
            edits: Optional message to edit
        """
        await evt.respond(message, allow_html=True, edits=edits)
    
    def is_tracking_enabled(self) -> bool:
        """Check if user tracking is enabled.
        
        Returns:
            bool: True if tracking is enabled
        """
        return self.config_manager.is_tracking_enabled()
    
    def is_verification_enabled(self) -> bool:
        """Check if verification is enabled.
        
        Returns:
            bool: True if verification is enabled
        """
        return self.config_manager.is_verification_enabled()
    
    def get_parent_room(self) -> Optional[str]:
        """Get the parent room ID.
        
        Returns:
            str: Parent room ID or None
        """
        return self.config_manager.get_parent_room()


class TrackingCommandHandler(BaseCommandHandler):
    """Base handler for commands that require user tracking."""
    
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute command with tracking check."""
        if not self.is_tracking_enabled():
            await self.reply_error(evt, "user tracking is disabled")
            return
        return await self.execute_tracking_command(evt, *args, **kwargs)
    
    @abstractmethod
    async def execute_tracking_command(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the tracking command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass


class AdminCommandHandler(BaseCommandHandler):
    """Base handler for admin-only commands."""
    
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute command with admin permission check."""
        if not await self.check_permissions(evt, min_level=100):
            await self.reply_error(evt, "You don't have permission to use this command")
            return
        return await self.execute_admin_command(evt, *args, **kwargs)
    
    @abstractmethod
    async def execute_admin_command(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the admin command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass


class ModeratorCommandHandler(BaseCommandHandler):
    """Base handler for moderator commands."""
    
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute command with moderator permission check."""
        if not await self.check_permissions(evt, min_level=50):
            await self.reply_error(evt, "You don't have permission to use this command")
            return
        return await self.execute_moderator_command(evt, *args, **kwargs)
    
    @abstractmethod
    async def execute_moderator_command(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the moderator command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass


class SpaceCommandHandler(BaseCommandHandler):
    """Base handler for commands that require parent space."""
    
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute command with parent space check."""
        if not await self.check_parent_room(evt):
            return
        return await self.execute_space_command(evt, *args, **kwargs)
    
    @abstractmethod
    async def execute_space_command(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the space command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass


class SpaceModeratorCommandHandler(SpaceCommandHandler, ModeratorCommandHandler):
    """Base handler for commands that require both parent space and moderator permissions."""
    
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute command with both space and moderator checks."""
        if not await self.check_parent_room(evt):
            return
        if not await self.check_permissions(evt, min_level=50):
            await self.reply_error(evt, "You don't have permission to use this command")
            return
        return await self.execute_space_moderator_command(evt, *args, **kwargs)
    
    @abstractmethod
    async def execute_space_moderator_command(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the space moderator command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass


class SpaceAdminCommandHandler(SpaceCommandHandler, AdminCommandHandler):
    """Base handler for commands that require both parent space and admin permissions."""
    
    async def execute(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute command with both space and admin checks."""
        if not await self.check_parent_room(evt):
            return
        if not await self.check_permissions(evt, min_level=100):
            await self.reply_error(evt, "You don't have permission to use this command")
            return
        return await self.execute_space_admin_command(evt, *args, **kwargs)
    
    @abstractmethod
    async def execute_space_admin_command(self, evt: MessageEvent, *args, **kwargs) -> Any:
        """Execute the space admin command logic.
        
        Args:
            evt: Message event
            *args: Command arguments
            **kwargs: Additional keyword arguments
            
        Returns:
            Command result
        """
        pass
