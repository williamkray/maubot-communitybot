"""Response building utilities for the community bot."""

from typing import List, Dict, Any, Optional
from mautrix.types import MessageEvent


class ResponseBuilder:
    """Builder for consistent response formatting."""
    
    @staticmethod
    def build_html_response(title: str, content: str, allow_html: bool = True) -> str:
        """Build an HTML formatted response.
        
        Args:
            title: Response title
            content: Response content
            allow_html: Whether to allow HTML formatting
            
        Returns:
            str: Formatted response
        """
        if allow_html:
            return f"<p><b>{title}</b><br />{content}</p>"
        else:
            return f"{title}\n{content}"
    
    @staticmethod
    def build_error_response(error: str, allow_html: bool = True) -> str:
        """Build an error response.
        
        Args:
            error: Error message
            allow_html: Whether to allow HTML formatting
            
        Returns:
            str: Formatted error response
        """
        if allow_html:
            return f"<p><b>Error:</b> {error}</p>"
        else:
            return f"Error: {error}"
    
    @staticmethod
    def build_success_response(message: str, allow_html: bool = True) -> str:
        """Build a success response.
        
        Args:
            message: Success message
            allow_html: Whether to allow HTML formatting
            
        Returns:
            str: Formatted success response
        """
        if allow_html:
            return f"<p><b>Success:</b> {message}</p>"
        else:
            return f"Success: {message}"
    
    @staticmethod
    def build_list_response(title: str, items: List[str], allow_html: bool = True) -> str:
        """Build a list response.
        
        Args:
            title: List title
            items: List items
            allow_html: Whether to allow HTML formatting
            
        Returns:
            str: Formatted list response
        """
        if not items:
            return ResponseBuilder.build_html_response(title, "No items found.", allow_html)
        
        if allow_html:
            items_html = "<br />".join(items)
            return f"<p><b>{title}</b><br />{items_html}</p>"
        else:
            items_text = "\n".join(f"- {item}" for item in items)
            return f"{title}\n{items_text}"
    
    @staticmethod
    def build_room_link(alias: str, server: str) -> str:
        """Build a Matrix room link.
        
        Args:
            alias: Room alias
            server: Server name
            
        Returns:
            str: HTML room link
        """
        return f"<a href='https://matrix.to/#/#{alias}:{server}'>#{alias}:{server}</a>"
    
    @staticmethod
    def build_user_link(user_id: str) -> str:
        """Build a Matrix user link.
        
        Args:
            user_id: User ID
            
        Returns:
            str: HTML user link
        """
        return f"<a href='https://matrix.to/#/{user_id}'>{user_id}</a>"
    
    @staticmethod
    def build_activity_report_response(report: Dict[str, List[str]], config: Dict[str, Any]) -> str:
        """Build an activity report response.
        
        Args:
            report: Activity report data
            config: Bot configuration
            
        Returns:
            str: Formatted activity report
        """
        warn_threshold = config.get("warn_threshold_days", 30)
        kick_threshold = config.get("kick_threshold_days", 60)
        
        response_parts = []
        
        if report.get("warn_inactive"):
            warn_list = "<br />".join(report["warn_inactive"])
            response_parts.append(
                f"<p><b>Users inactive for between {warn_threshold} and {kick_threshold} days:</b><br />"
                f"{warn_list}<br /></p>"
            )
        
        if report.get("kick_inactive"):
            kick_list = "<br />".join(report["kick_inactive"])
            response_parts.append(
                f"<p><b>Users inactive for at least {kick_threshold} days:</b><br />"
                f"{kick_list}<br /></p>"
            )
        
        if report.get("ignored"):
            ignored_list = "<br />".join(report["ignored"])
            response_parts.append(
                f"<p><b>Ignored users:</b><br />{ignored_list}</p>"
            )
        
        return "".join(response_parts)
    
    @staticmethod
    def build_ban_results_response(results: Dict[str, Any]) -> str:
        """Build a ban results response.
        
        Args:
            results: Ban results data
            
        Returns:
            str: Formatted ban results
        """
        ban_list = results.get("ban_list", [])
        error_list = results.get("error_list", [])
        
        response_parts = []
        
        if ban_list:
            ban_list_html = "<br />".join(ban_list)
            response_parts.append(f"<p><b>Users banned:</b><br /><code>{ban_list_html}</code></p>")
        
        if error_list:
            error_list_html = "<br />".join(error_list)
            response_parts.append(f"<p><b>Errors:</b><br /><code>{error_list_html}</code></p>")
        
        if not response_parts:
            response_parts.append("<p>No users were banned.</p>")
        
        return "".join(response_parts)
    
    @staticmethod
    def build_sync_results_response(results: Dict[str, List[str]]) -> str:
        """Build a sync results response.
        
        Args:
            results: Sync results data
            
        Returns:
            str: Formatted sync results
        """
        added = results.get("added", [])
        dropped = results.get("dropped", [])
        
        response_parts = []
        
        if added:
            added_html = "<br />".join(added)
            response_parts.append(f"<p><b>Added:</b><br />{added_html}</p>")
        
        if dropped:
            dropped_html = "<br />".join(dropped)
            response_parts.append(f"<p><b>Dropped:</b><br />{dropped_html}</p>")
        
        if not response_parts:
            response_parts.append("<p>No changes made.</p>")
        
        return "".join(response_parts)
    
    @staticmethod
    def build_doctor_report_response(report: Dict[str, Any]) -> str:
        """Build a doctor report response.
        
        Args:
            report: Doctor report data
            
        Returns:
            str: Formatted doctor report
        """
        response_parts = []
        
        # Space information
        if report.get("space"):
            space = report["space"]
            space_info = f"<b>Space:</b> {space.get('room_id', 'Unknown')}<br />"
            space_info += f"Bot Power Level: {space.get('bot_power_level', 'Unknown')}<br />"
            space_info += f"Has Admin: {space.get('has_admin', False)}<br />"
            response_parts.append(f"<p>{space_info}</p>")
        
        # Room information
        if report.get("rooms"):
            rooms_info = "<b>Rooms:</b><br />"
            for room_id, room_data in report["rooms"].items():
                rooms_info += f"- {room_id}: {room_data.get('status', 'Unknown')}<br />"
            response_parts.append(f"<p>{rooms_info}</p>")
        
        # Issues
        if report.get("issues"):
            issues_html = "<br />".join(report["issues"])
            response_parts.append(f"<p><b>Issues:</b><br />{issues_html}</p>")
        
        # Warnings
        if report.get("warnings"):
            warnings_html = "<br />".join(report["warnings"])
            response_parts.append(f"<p><b>Warnings:</b><br />{warnings_html}</p>")
        
        if not response_parts:
            response_parts.append("<p>No issues found.</p>")
        
        return "".join(response_parts)
