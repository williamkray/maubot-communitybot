"""Configuration management utilities for the community bot."""

from typing import List, Dict, Any, Optional


class ConfigManager:
    """Centralized configuration management for the community bot."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize with bot configuration.

        Args:
            config: Bot configuration dictionary
        """
        self.config = config

    def is_tracking_enabled(self) -> bool:
        """Check if user tracking is enabled.

        Returns:
            bool: True if tracking is enabled
        """
        track_users = self.config.get("track_users", [])

        # Handle legacy boolean configuration
        if isinstance(track_users, bool):
            return track_users

        # Handle new list configuration
        return isinstance(track_users, list) and len(track_users) > 0

    def is_message_tracking_enabled(self) -> bool:
        """Check if message tracking is enabled.

        Returns:
            bool: True if message tracking is enabled
        """
        track_users = self.config.get("track_users", [])

        # Handle legacy boolean configuration - if True, enable both messages and reactions
        if isinstance(track_users, bool):
            return track_users

        # Handle new list configuration
        return isinstance(track_users, list) and "messages" in track_users

    def is_reaction_tracking_enabled(self) -> bool:
        """Check if reaction tracking is enabled.

        Returns:
            bool: True if reaction tracking is enabled
        """
        track_users = self.config.get("track_users", [])

        # Handle legacy boolean configuration - if True, enable both messages and reactions
        if isinstance(track_users, bool):
            return track_users

        # Handle new list configuration
        return isinstance(track_users, list) and "reactions" in track_users

    def is_verification_enabled(self) -> bool:
        """Check if verification is enabled.

        Returns:
            bool: True if verification is enabled
        """
        return self.config.get("verification_enabled", False)

    def is_proactive_banning_enabled(self) -> bool:
        """Check if proactive banning is enabled.

        Returns:
            bool: True if proactive banning is enabled
        """
        return self.config.get("proactive_banning", False)

    def is_encryption_enabled(self) -> bool:
        """Check if encryption is enabled by default.

        Returns:
            bool: True if encryption is enabled
        """
        return self.config.get("encrypt", False)

    def get_room_version(self) -> str:
        """Get the configured room version.

        Returns:
            str: Room version string
        """
        return self.config.get("room_version", "1")

    def get_community_slug(self) -> Optional[str]:
        """Get the community slug.

        Returns:
            str: Community slug or None if not configured
        """
        return self.config.get("community_slug")

    def get_use_community_slug(self) -> Optional[str]:
        """Get the community slug suffix setting.

        Returns:
            bool: Whether to use the community slug as a room suffix
        """
        return self.config.get("use_community_slug")

    def get_parent_room(self) -> Optional[str]:
        """Get the parent room ID.

        Returns:
            str: Parent room ID or None if not configured
        """
        return self.config.get("parent_room")

    def get_invitees(self) -> List[str]:
        """Get the list of users to invite to new rooms.

        Returns:
            List[str]: List of user IDs to invite
        """
        return self.config.get("invitees", [])

    def get_invite_power_level(self) -> int:
        """Get the power level required to invite users.

        Returns:
            int: Power level for inviting users
        """
        return self.config.get("invite_power_level", 50)

    def get_sleep_duration(self) -> float:
        """Get the sleep duration between operations.

        Returns:
            float: Sleep duration in seconds
        """
        return self.config.get("sleep", 1.0)

    def get_welcome_sleep_duration(self) -> float:
        """Get the sleep duration for welcome messages.

        Returns:
            float: Welcome sleep duration in seconds
        """
        return self.config.get("welcome_sleep", 2.0)

    def get_warn_threshold_days(self) -> int:
        """Get the warning threshold for inactive users.

        Returns:
            int: Number of days before warning
        """
        return self.config.get("warn_threshold_days", 30)

    def get_kick_threshold_days(self) -> int:
        """Get the kick threshold for inactive users.

        Returns:
            int: Number of days before kicking
        """
        return self.config.get("kick_threshold_days", 60)

    def get_verification_phrase(self) -> str:
        """Get the verification phrase.

        Returns:
            str: Verification phrase
        """
        return self.config.get("verification_phrase", "I agree to the rules")

    def get_verification_attempts(self) -> int:
        """Get the maximum verification attempts.

        Returns:
            int: Maximum verification attempts
        """
        return self.config.get("verification_attempts", 3)

    def get_verification_timeout(self) -> int:
        """Get the verification timeout in seconds.

        Returns:
            int: Verification timeout in seconds
        """
        return self.config.get("verification_timeout", 300)

    def get_banlist_rooms(self) -> List[str]:
        """Get the list of banlist rooms.

        Returns:
            List[str]: List of banlist room IDs or aliases
        """
        return self.config.get("banlist_rooms", [])

    def get_redaction_rooms(self) -> List[str]:
        """Get the list of rooms for redaction.

        Returns:
            List[str]: List of room IDs for redaction
        """
        return self.config.get("redaction_rooms", [])

    def validate_required_configs(self) -> List[str]:
        """Validate that all required configurations are present.

        Returns:
            List[str]: List of missing required configuration keys
        """
        required_configs = [
            "parent_room",
            "room_version",
            "community_slug",
            "use_community_slug",
        ]

        missing = []
        for config_key in required_configs:
            if not self.config.get(config_key):
                missing.append(config_key)

        return missing

    def is_modern_room_version(self) -> bool:
        """Check if the configured room version is modern (12+).

        Returns:
            bool: True if room version is 12 or higher
        """
        try:
            version = int(self.get_room_version())
            return version >= 12
        except (ValueError, TypeError):
            return False

    def get_room_creation_settings(self) -> Dict[str, Any]:
        """Get settings specific to room creation.

        Returns:
            Dict[str, Any]: Room creation settings
        """
        return {
            "room_version": self.get_room_version(),
            "community_slug": self.get_community_slug(),
            "use_community_slug": self.get_use_community_slug(),
            "invitees": self.get_invitees(),
            "invite_power_level": self.get_invite_power_level(),
            "encrypt": self.is_encryption_enabled(),
            "parent_room": self.get_parent_room(),
        }

    def get_tracking_settings(self) -> Dict[str, Any]:
        """Get settings specific to user tracking.

        Returns:
            Dict[str, Any]: Tracking settings
        """
        return {
            "track_users": self.config.get("track_users", []),
            "track_messages": self.is_message_tracking_enabled(),
            "track_reactions": self.is_reaction_tracking_enabled(),
            "warn_threshold_days": self.get_warn_threshold_days(),
            "kick_threshold_days": self.get_kick_threshold_days(),
        }

    def get_verification_settings(self) -> Dict[str, Any]:
        """Get settings specific to verification.

        Returns:
            Dict[str, Any]: Verification settings
        """
        return {
            "verification_enabled": self.is_verification_enabled(),
            "verification_phrase": self.get_verification_phrase(),
            "verification_attempts": self.get_verification_attempts(),
            "verification_timeout": self.get_verification_timeout(),
        }
