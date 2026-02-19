# kickbot - a maubot plugin to track user activity and remove inactive users from rooms/spaces.

from typing import Awaitable, Type, Optional, Tuple, Dict
import json
import time
import re
import fnmatch
import asyncio
import random
import asyncpg.exceptions
from datetime import datetime

from mautrix.client import (
    Client,
    InternalEventType,
    MembershipEventDispatcher,
    SyncStream,
)
from mautrix.types import (
    Event,
    StateEvent,
    EventID,
    UserID,
    FileInfo,
    EventType,
    MediaMessageEventContent,
    ReactionEvent,
    RedactionEvent,
    RoomID,
    RoomAlias,
    PowerLevelStateEventContent,
    MessageType,
    PaginationDirection,
    SpaceChildStateEventContent,
    SpaceParentStateEventContent,
    JoinRulesStateEventContent,
    JoinRule,
    RoomCreatePreset,
)
from mautrix.errors import MNotFound
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event

BAN_STATE_EVENT = EventType.find("m.policy.rule.user", EventType.Class.STATE)

# database table related things
from .db import upgrade_table

# Helper modules
from .helpers import (
    message_utils,
    room_utils,
    user_utils,
    database_utils,
    report_utils,
    decorators,
    common_utils,
    room_creation_utils,
    config_manager,
    response_builder,
    diagnostic_utils,
    base_command_handler,
)


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("sleep")
        helper.copy("welcome_sleep")
        helper.copy("parent_room")
        helper.copy("community_slug")
        helper.copy("use_community_slug")
        helper.copy("track_users")
        helper.copy("warn_threshold_days")
        helper.copy("kick_threshold_days")
        helper.copy("encrypt")
        helper.copy("invitees")
        helper.copy("notification_room")
        helper.copy("join_notification_message")
        helper.copy_dict("greeting_rooms")
        helper.copy_dict("greetings")
        helper.copy("censor")
        helper.copy("uncensor_pl")
        helper.copy("censor_wordlist")
        helper.copy("censor_wordlist_instaban")
        helper.copy("censor_files")
        helper.copy("banlists")
        helper.copy("proactive_banning")
        helper.copy("redact_on_ban")
        helper.copy("check_if_human")
        helper.copy("verification_phrases")
        helper.copy("verification_attempts")
        helper.copy("verification_message")
        helper.copy("invite_power_level")
        helper.copy("room_version")


class CommunityBot(Plugin):

    _redaction_tasks: asyncio.Task = None
    _verification_states: Dict[str, Dict] = {}

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.config_manager = config_manager.ConfigManager(self.config)
        self.client.add_dispatcher(MembershipEventDispatcher)
        # Start background redaction task
        self._redaction_tasks = asyncio.create_task(self._redaction_loop())
        # Clean up stale verification states
        await self.cleanup_stale_verification_states()

    async def stop(self) -> None:
        if self._redaction_tasks:
            self._redaction_tasks.cancel()
        await super().stop()

    async def user_permitted(
        self, user_id: UserID, min_level: int = 50, room_id: str = None
    ) -> bool:
        """Check if a user has sufficient power level in a room.

        Args:
            user_id: The Matrix ID of the user to check
            min_level: Minimum required power level (default 50 for moderator)
            room_id: The room ID to check permissions in. If None, uses parent room.

        Returns:
            bool: True if user has sufficient power level
        """
        return await user_utils.user_permitted(
            self.client,
            user_id,
            self.config["parent_room"],
            min_level,
            room_id,
            self.log,
        )

    def generate_community_slug(self, community_name: str) -> str:
        """Generate a community slug from the community name.

        Args:
            community_name: The full community name

        Returns:
            str: A slug made from the first letter of each word, lowercase
        """
        return message_utils.generate_community_slug(community_name)

    async def validate_room_alias(self, alias_localpart: str, server: str) -> bool:
        """Check if a room alias already exists.

        Args:
            alias_localpart: The localpart of the alias (without # and :server)
            server: The server domain

        Returns:
            bool: True if alias is available, False if it already exists
        """
        return await room_utils.validate_room_alias(
            self.client, alias_localpart, server
        )

    async def validate_room_aliases(
        self, room_names: list[str], evt: MessageEvent = None
    ) -> tuple[bool, list[str]]:
        """Validate that all room aliases are available.

        Args:
            room_names: List of room names to validate
            evt: Optional MessageEvent for progress updates

        Returns:
            tuple: (is_valid, list_of_conflicting_aliases)
        """
        if self.config.get("use_community_slug", True) and not self.config.get(
            "community_slug", ""
        ):
            if evt:
                await evt.respond(
                    "Error: No community slug configured. Please run initialize command first."
                )
            return False, []

        server = self.client.parse_user_id(self.client.mxid)[1]
        return await room_utils.validate_room_aliases(
            self.client,
            room_names,
            self.config.get("community_slug", ""),
            self.config.get("use_community_slug", True),
            server,
        )

    async def get_moderators_and_above(self) -> list[str]:
        """Get list of users with moderator or higher permissions from the parent space.

        Returns:
            list: List of user IDs with power level >= 50 (moderator or above)
        """
        return await room_utils.get_moderators_and_above(
            self.client, self.config.get("parent_room", "")
        )

    async def create_space(
        self,
        space_name: str,
        evt: MessageEvent = None,
        power_level_override: Optional[PowerLevelStateEventContent] = None,
    ) -> tuple[str, str]:
        """Create a new space without community slug suffix.

        Args:
            space_name: The name for the new space
            evt: Optional MessageEvent for progress updates
            power_level_override: Optional power levels to use

        Returns:
            tuple: (space_id, space_alias) if successful, None if failed
        """
        mymsg = None
        try:
            sanitized_name = re.sub(r"[^a-zA-Z0-9]", "", space_name).lower()
            invitees = self.config.get("invitees", [])
            server = self.client.parse_user_id(self.client.mxid)[1]

            # Validate that the space alias is available
            is_available = await self.validate_room_alias(sanitized_name, server)
            if not is_available:
                error_msg = f"Space alias #{sanitized_name}:{server} already exists. Cannot create space."
                self.log.error(error_msg)
                if evt:
                    await evt.respond(error_msg)
                return None, None

            if evt:
                mymsg = await evt.respond(
                    f"creating space {sanitized_name} with room version {self.config.get('room_version', '1')}, give me a minute..."
                )

            # Prepare creation content with space type
            # Spaces are created by setting the type to "m.space" in creation_content
            creation_content = {
                "type": "m.space",
                "m.federate": True,
                "m.room.history_visibility": "joined",
            }

            # For modern room versions (12+), remove the bot from power levels
            # as creators have unlimited power by default and cannot appear in power levels
            if (
                self.is_modern_room_version(self.config.get("room_version", "1"))
                and power_level_override
            ):
                self.log.info(
                    f"Modern room version {self.config.get('room_version', '1')} detected - removing bot from power levels"
                )
                if power_level_override.users:
                    # Remove bot from users list but keep other important settings
                    power_level_override.users.pop(self.client.mxid, None)

            # Create the space with space-specific content
            # Note: room_version is set via the room_version parameter, not creation_content
            self.log.info(
                f"Creating space with room_version={self.config.get('room_version', '1')}"
            )
            self.log.info(f"Creation content: {creation_content}")
            self.log.info(f"Calling client.create_room with parameters:")
            self.log.info(f"  - alias_localpart: {sanitized_name}")
            self.log.info(f"  - name: {space_name}")
            self.log.info(f"  - invitees: {invitees}")
            self.log.info(f"  - power_level_override: {power_level_override}")
            self.log.info(f"  - creation_content: {creation_content}")
            self.log.info(f"  - room_version: {self.config.get('room_version', '1')}")

            space_id = await self.client.create_room(
                alias_localpart=sanitized_name,
                name=space_name,
                invitees=invitees,
                power_level_override=power_level_override,
                creation_content=creation_content,
                room_version=self.config.get("room_version", "1"),
            )

            # Verify the space version and type were set correctly
            try:
                actual_version, actual_creators = (
                    await self.get_room_version_and_creators(space_id)
                )
                self.log.info(
                    f"Space {space_id} created with version {actual_version} (requested: {self.config.get('room_version', '1')})"
                )
                if actual_version != self.config.get("room_version", "1"):
                    self.log.warning(
                        f"Space version mismatch: requested {self.config.get('room_version', '1')}, got {actual_version}"
                    )

                # Verify the space type was set
                state_events = await self.client.get_state(space_id)
                space_type_set = False
                for event in state_events:
                    if event.type == EventType.ROOM_CREATE:
                        space_type = event.content.get("type")
                        self.log.info(f"Space creation event type: {space_type}")
                        space_type_set = space_type == "m.space"
                        break

                if not space_type_set:
                    self.log.error(f"Space type was not set correctly in {space_id}")
                    # Try to set the space type after creation as a fallback
                    try:
                        self.log.info(
                            f"Attempting to set space type after creation for {space_id}"
                        )
                        await self.client.send_state_event(
                            space_id,
                            EventType.ROOM_CREATE,
                            {"type": "m.space"},
                            state_key="",
                        )
                        self.log.info(
                            f"Successfully set space type after creation for {space_id}"
                        )
                    except Exception as e2:
                        self.log.error(f"Failed to set space type after creation: {e2}")
                else:
                    self.log.info(f"Space type verified as 'm.space' in {space_id}")

            except Exception as e:
                self.log.warning(f"Could not verify space creation: {e}")

            if evt:
                await evt.respond(
                    f"<a href='https://matrix.to/#/#{sanitized_name}:{server}'>#{sanitized_name}:{server}</a> has been created.",
                    edits=mymsg,
                    allow_html=True,
                )

            self.log.info(f"Space creation completed successfully: {space_id}")
            return space_id, f"#{sanitized_name}:{server}"

        except Exception as e:
            error_msg = f"Failed to create space: {e}"
            self.log.error(error_msg)
            if evt and mymsg:
                await evt.respond(error_msg, edits=mymsg)
            elif evt:
                await evt.respond(error_msg)
            return None, None

    async def _redaction_loop(self) -> None:
        while True:
            try:
                # Get all rooms with pending redactions
                rooms = await self.database.fetch(
                    "SELECT DISTINCT room_id FROM redaction_tasks"
                )
                for room in rooms:
                    await self.redact_messages(room["room_id"])
                await asyncio.sleep(60)  # Run every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error(f"Error in redaction loop: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying on error

    async def do_sync(self) -> None:
        if not self.config_manager.is_tracking_enabled():
            return "user tracking is disabled"

        try:
            space_members_obj = await self.client.get_joined_members(
                self.config["parent_room"]
            )
            space_members_list = space_members_obj.keys()
        except asyncpg.exceptions.UniqueViolationError as e:
            # If we hit a duplicate key error, log it and retry once
            self.log.warning(f"Duplicate key error during member sync, retrying: {e}")
            await asyncio.sleep(1)  # Brief delay before retry
            space_members_obj = await self.client.get_joined_members(
                self.config["parent_room"]
            )
            space_members_list = space_members_obj.keys()
        except Exception as e:
            self.log.error(f"Failed to get space members: {e}")
            return {"added": [], "dropped": []}

        table_users = await self.database.fetch("SELECT mxid FROM user_events")
        table_user_list = [row["mxid"] for row in table_users]
        untracked_users = set(space_members_list) - set(table_user_list)
        non_space_members = set(table_user_list) - set(space_members_list)
        results = {}
        results["added"] = []
        results["dropped"] = []
        try:
            for user in untracked_users:
                now = int(time.time() * 1000)
                q = """
                    INSERT INTO user_events (mxid, last_message_timestamp)
                    VALUES ($1, $2)
                    """
                await self.database.execute(q, user, now)
                results["added"].append(user)
                self.log.info(f"{user} inserted into activity tracking table")
            for user in non_space_members:
                await self.database.execute(
                    "DELETE FROM user_events WHERE mxid = $1", user
                )
                self.log.info(
                    f"{user} is not a space member, dropped from activity tracking table"
                )
                results["dropped"].append(user)

        except Exception as e:
            self.log.error(f"Error syncing space members: {e}")

        return results

    async def get_space_roomlist(self) -> list[str]:
        space = self.config["parent_room"]
        rooms = []

        # Check if parent room is configured
        if not space:
            self.log.warning("No parent room configured, cannot get space roomlist")
            return rooms

        try:
            self.log.debug(f"DEBUG getting roomlist from {space} space")
            state = await self.client.get_state(space)
            for evt in state:
                if evt.type == EventType.SPACE_CHILD:
                    # only look for rooms that include a via path, otherwise they
                    # are not really in the space!
                    if evt.content and evt.content.via:
                        rooms.append(evt.state_key)
        except Exception as e:
            self.log.error(f"Error getting space roomlist: {e}")
        return rooms

    async def generate_report(self) -> None:
        now = int(time.time() * 1000)
        warn_days_ago = now - (1000 * 60 * 60 * 24 * self.config["warn_threshold_days"])
        kick_days_ago = now - (1000 * 60 * 60 * 24 * self.config["kick_threshold_days"])
        warn_q = """
            SELECT mxid FROM user_events WHERE last_message_timestamp <= $1 AND 
            last_message_timestamp >= $2
            AND (ignore_inactivity < 1 OR ignore_inactivity IS NULL)
            """
        kick_q = """
            SELECT mxid FROM user_events WHERE last_message_timestamp <= $1
            AND (ignore_inactivity < 1 OR ignore_inactivity IS NULL)
            """
        ignored_q = """
            SELECT mxid FROM user_events WHERE ignore_inactivity = 1
            """
        warn_inactive_results = await self.database.fetch(
            warn_q, warn_days_ago, kick_days_ago
        )
        kick_inactive_results = await self.database.fetch(kick_q, kick_days_ago)
        ignored_results = await self.database.fetch(ignored_q)

        database_results = {
            "warn_inactive": warn_inactive_results,
            "kick_inactive": kick_inactive_results,
            "ignored": ignored_results,
        }

        return report_utils.generate_activity_report(database_results)

    def flag_message(self, msg):
        return message_utils.flag_message(
            msg, self.config["censor_wordlist"], self.config["censor_files"]
        )

    def flag_instaban(self, msg):
        return message_utils.flag_instaban(msg, self.config["censor_wordlist_instaban"])

    def censor_room(self, msg):
        return message_utils.censor_room(msg, self.config["censor"])

    async def check_if_banned(self, userid):
        return await user_utils.check_if_banned(
            self.client, userid, self.config["banlists"], self.log
        )

    async def get_messages_to_redact(self, room_id, mxid):
        return await database_utils.get_messages_to_redact(
            self.client, room_id, mxid, self.log
        )

    async def redact_messages(self, room_id):
        return await database_utils.redact_messages(
            self.client, self.database, room_id, self.config["sleep"], self.log
        )

    async def check_bot_permissions(
        self,
        room_id: str,
        evt: MessageEvent = None,
        required_permissions: list[str] = None,
    ) -> tuple[bool, str, dict]:
        """Check if the bot has necessary permissions in a room.

        Args:
            room_id: The ID of the room to check permissions in
            evt: Optional MessageEvent for progress updates
            required_permissions: List of specific permissions to check. If None, checks basic room access.

        Returns:
            tuple: (has_permissions, error_message, permission_details)
        """
        try:
            # Check if bot is in the room
            try:
                await self.client.get_state_event(
                    room_id, EventType.ROOM_MEMBER, self.client.mxid
                )
            except MNotFound:
                return False, "Bot is not a member of this room", {}

            # Check if bot has unlimited power (creator in modern room versions)
            if await self.user_has_unlimited_power(self.client.mxid, room_id):
                return True, "", {"unlimited_power": True}

            # Get power levels
            power_levels = await self.client.get_state_event(
                room_id, EventType.ROOM_POWER_LEVELS
            )
            bot_level = power_levels.users.get(
                self.client.mxid, power_levels.users_default
            )

            # Define required power levels for different actions
            permission_requirements = {
                "redact": power_levels.redact,
                "kick": power_levels.kick,
                "ban": power_levels.ban,
                "invite": power_levels.invite,
                "tombstone": power_levels.events.get(
                    "m.room.tombstone", power_levels.events_default
                ),
                "power_levels": power_levels.events.get(
                    "m.room.power_levels", power_levels.events_default
                ),
                "state": power_levels.state_default,
            }

            # Check each required permission
            permission_status = {}
            if required_permissions:
                for perm in required_permissions:
                    if perm in permission_requirements:
                        required_level = permission_requirements[perm]
                        permission_status[perm] = {
                            "has_permission": bot_level >= required_level,
                            "required_level": required_level,
                            "bot_level": bot_level,
                        }

            # If no specific permissions requested, just check basic access
            if not required_permissions:
                if bot_level < 50:  # Basic moderator level
                    return (
                        False,
                        "Bot does not have sufficient power level (needs at least moderator level)",
                        permission_status,
                    )
                return True, "", permission_status

            # Check if all requested permissions are granted
            missing_permissions = [
                perm
                for perm, status in permission_status.items()
                if not status["has_permission"]
            ]

            if missing_permissions:
                error_msg = "Bot is missing required permissions: " + ", ".join(
                    missing_permissions
                )
                return False, error_msg, permission_status

            return True, "", permission_status

        except Exception as e:
            error_msg = f"Failed to check bot permissions: {e}"
            self.log.error(error_msg)
            if evt:
                await evt.respond(error_msg)
            return False, error_msg, {}

    async def do_archive_room(
        self, room_id: str, evt: MessageEvent = None, replacement_room: str = ""
    ) -> bool:
        """Handle common room archival activities like removing from space, removing aliases, and setting tombstone.

        Args:
            room_id: The ID of the room to archive
            evt: Optional MessageEvent for progress updates
            replacement_room: Optional room ID to point to in the tombstone event

        Returns:
            bool: True if all operations succeeded, False otherwise
        """
        try:
            # Check permissions for all required operations
            has_perms, error_msg, _ = await self.check_bot_permissions(
                room_id, evt, ["state", "tombstone", "power_levels"]
            )
            if not has_perms:
                if evt:
                    await evt.respond(f"Cannot archive room: {error_msg}")
                return False

            # Try to remove the room from the space first
            self.log.debug(f"DEBUG removing space state reference from {room_id}")
            await self.client.send_state_event(
                room_id=room_id,
                event_type="m.space.parent",
                content={},  # Empty content removes the state
                state_key=self.config["parent_room"],
            )
            self.log.info(f"Removed parent space reference from room {room_id}")

            # Remove the child reference from the space
            self.log.debug(
                f"DEBUG removing child state reference from {self.config['parent_room']}"
            )
            await self.client.send_state_event(
                self.config["parent_room"],
                event_type="m.space.child",
                content={},  # Empty content removes the state
                state_key=room_id,
            )
            self.log.info(
                f"Removed child room reference from space {self.config['parent_room']}"
            )

            # Remove room aliases to release them
            await self.remove_room_aliases(room_id, evt)

            # Send the tombstone
            tombstone_content = {
                "body": (
                    "This room has been archived."
                    if not replacement_room
                    else "This room has been replaced. Please join the new room."
                ),
                "replacement_room": replacement_room,
            }
            await self.client.send_state_event(
                room_id=room_id,
                event_type=EventType.ROOM_TOMBSTONE,
                content=tombstone_content,
            )
            self.log.info(f"Successfully added tombstone to room {room_id}")

            return True

        except Exception as e:
            error_msg = f"Failed to archive room: {e}"
            self.log.error(error_msg)
            if evt:
                await evt.respond(error_msg)
            return False

    async def remove_room_aliases(self, room_id: str, evt: MessageEvent = None) -> list:
        """Remove all aliases from a room.

        Args:
            room_id: The ID of the room whose aliases to remove
            evt: Optional MessageEvent for progress updates

        Returns:
            list: List of aliases that were successfully removed
        """
        removed_aliases = []

        try:
            aliases = await self.client.get_state_event(
                room_id=room_id, event_type=EventType.ROOM_CANONICAL_ALIAS
            )
        except Exception as e:
            self.log.warning(f"Failed to get room alias state event, skipping: {e}")
            return removed_aliases

        if aliases.alt_aliases:
            for alias in aliases.alt_aliases:
                try:
                    await self.client.remove_room_alias(
                        alias_localpart=alias.split(":")[0].lstrip("#"),
                    )
                    self.log.info(f"Removed alias {alias} from room {room_id}")
                    removed_aliases.append(alias)
                except Exception as e:
                    self.log.warning(f"Failed to remove alias {alias}: {e}")

        if aliases.canonical_alias:
            try:
                await self.client.remove_room_alias(
                    alias_localpart=aliases.canonical_alias.split(":")[0].lstrip("#"),
                )
                self.log.info(
                    f"Removed canonical alias {aliases.canonical_alias} from room {room_id}"
                )
                removed_aliases.append(aliases.canonical_alias)
            except Exception as e:
                self.log.warning(f"Failed to remove canonical alias: {e}")

        return removed_aliases

    async def ban_this_user(self, user, reason="banned", all_rooms=False):
        roomlist = await self.get_space_roomlist()
        # don't forget to kick from the space itself
        roomlist.append(self.config["parent_room"])

        return await user_utils.ban_user_from_rooms(
            self.client,
            user,
            roomlist,
            reason,
            all_rooms,
            self.config["redact_on_ban"],
            self.get_messages_to_redact,
            self.database,
            self.config["sleep"],
            self.log,
        )

    async def get_banlist_roomids(self):
        return await user_utils.get_banlist_roomids(
            self.client, self.config["banlists"], self.log
        )

    async def get_room_version_and_creators(
        self, room_id: str
    ) -> tuple[str, list[str]]:
        """Get the room version and creators for a room.

        Args:
            room_id: The room ID to check

        Returns:
            tuple: (room_version, list_of_creators)
        """
        return await room_utils.get_room_version_and_creators(self.client, room_id)

    def is_modern_room_version(self, room_version: str) -> bool:
        """Check if a room version is 12 or newer (modern room versions).

        Args:
            room_version: The room version string to check

        Returns:
            bool: True if room version is 12 or newer
        """
        return room_utils.is_modern_room_version(room_version)

    async def user_has_unlimited_power(self, user_id: UserID, room_id: str) -> bool:
        """Check if a user has unlimited power in a room (creator in modern room versions).

        Args:
            user_id: The user ID to check
            room_id: The room ID to check in

        Returns:
            bool: True if user has unlimited power
        """
        return await room_utils.user_has_unlimited_power(self.client, user_id, room_id)

    @event.on(BAN_STATE_EVENT)
    async def check_ban_event(self, evt: StateEvent) -> None:
        if not self.config["proactive_banning"]:
            return

        banlist_roomids = await self.get_banlist_roomids()
        # we only care about ban events in rooms in the banlist
        if evt.room_id not in banlist_roomids:
            return
        else:
            try:
                entity = evt.content["entity"]
                recommendation = evt.content["recommendation"]
                self.log.debug(
                    f"DEBUG new ban rule found: {entity} should have action {recommendation}"
                )
                if bool(re.search(r"[*?]", entity)):
                    self.log.debug(
                        f"DEBUG ban rule appears to be glob pattern, skipping proactive measures."
                    )
                    return
                if bool(re.search("ban$", recommendation)):
                    await self.ban_this_user(entity)
            except Exception as e:
                self.log.error(e)

    @event.on(EventType.ROOM_POWER_LEVELS)
    async def sync_power_levels(self, evt: StateEvent) -> None:
        # Only care about changes in the parent room
        if evt.room_id != self.config["parent_room"]:
            return

        # Get the changed user and their new power level
        try:
            old_levels = evt.prev_content.get("users", {})
            new_levels = evt.content.get("users", {})

            # Find which user's power level changed
            changed_users = {}
            for user, new_level in new_levels.items():
                if user not in old_levels or old_levels[user] != new_level:
                    changed_users[user] = new_level

            if not changed_users:
                return

            # Get all rooms in the space
            space_rooms = await self.client.get_joined_rooms()
            success_rooms = []
            failed_rooms = []

            # Apply the same power level changes to each room
            for room_id in space_rooms:
                if room_id == self.config["parent_room"]:
                    continue

                roomname = await common_utils.get_room_name(
                    self.client, room_id, self.log
                )

                # Get current power levels
                try:
                    # Get current power levels
                    current_pl = await self.client.get_state_event(
                        room_id, EventType.ROOM_POWER_LEVELS
                    )

                    # Update existing power levels object with new levels
                    users = current_pl.get("users", {})
                    for user, level in changed_users.items():
                        users[user] = level

                    current_pl["users"] = users

                    # Send updated power levels
                    try:
                        await self.client.send_state_event(
                            room_id, EventType.ROOM_POWER_LEVELS, current_pl
                        )
                        success_rooms.append(roomname or room_id)
                    except Exception as e:
                        self.log.error(
                            f"Failed to send power levels to {roomname or room_id}: {e}"
                        )
                        failed_rooms.append(roomname or room_id)

                    time.sleep(self.config["sleep"])

                except Exception as e:
                    self.log.warning(f"Failed to update power levels in {room_id}: {e}")
                    failed_rooms.append(room_id)

            # Send notification if configured
            if self.config["notification_room"]:
                changes = ", ".join(
                    [f"{user} → {level}" for user, level in changed_users.items()]
                )
                notification = (
                    f"Power level changes ({changes}) propagated from parent room:<br>"
                )
                notification += (
                    f"Succeeded in: <code>{', '.join(success_rooms)}</code><br>"
                )
                if failed_rooms:
                    notification += f"Failed in: <code>{', '.join(failed_rooms)}</code>"

                await self.client.send_notice(
                    self.config["notification_room"], html=notification
                )

        except Exception as e:
            self.log.error(f"Error syncing power levels: {e}")

    async def handle_leave_events(self, evt: StateEvent) -> None:
        """Common logic for handling membership changes (leave/kick/ban)."""
        if evt.source & SyncStream.STATE:
            self.log.debug(
                f"Sync stream leave event for {evt.state_key} in {evt.room_id} detected"
            )
            return
        else:
            # check if the room the person left is protected by check_if_human
            # kick and ban events are sent by other people, so we need to use the state_key
            # when referring to the user who left
            user_id = evt.state_key
            self.log.debug(
                f"membership change event for {user_id} in {evt.room_id} detected"
            )
            if (
                isinstance(self.config["check_if_human"], bool)
                and self.config["check_if_human"]
            ) or (
                isinstance(self.config["check_if_human"], list)
                and evt.room_id in self.config["check_if_human"]
            ):
                self.log.debug(
                    f"Checking if {user_id} is a verified user in {evt.room_id}"
                )

                # Check if user has unlimited power (creator in modern room versions)
                if await self.user_has_unlimited_power(user_id, evt.room_id):
                    self.log.debug(
                        f"User {user_id} has unlimited power in {evt.room_id}, skipping power level cleanup"
                    )
                    return

                pl_state = await self.client.get_state_event(
                    evt.room_id, EventType.ROOM_POWER_LEVELS
                )
                try:
                    user_level = pl_state.get_user_level(user_id)
                except Exception as e:
                    self.log.error(
                        f"Failed to get user level for {user_id} in {evt.room_id}: {e}"
                    )
                    return
                default_level = pl_state.users_default
                self.log.debug(
                    f"User {user_id} has power level {user_level}, default level is {default_level}"
                )
                if user_level == (default_level + 1):  # indicates verified user
                    self.log.debug(
                        f"Removing {user_id} from power levels state event in {evt.room_id}"
                    )
                    pl_state.users.pop(user_id)
                    try:
                        await self.client.send_state_event(
                            evt.room_id, EventType.ROOM_POWER_LEVELS, pl_state
                        )
                    except Exception as e:
                        self.log.error(
                            f"Failed to update power levels state event in {evt.room_id}: {e}"
                        )

    @event.on(InternalEventType.LEAVE)
    async def handle_leave(self, evt: StateEvent) -> None:
        """Handle voluntary leave events."""
        await self.handle_leave_events(evt)

    @event.on(InternalEventType.KICK)
    async def handle_kick(self, evt: StateEvent) -> None:
        """Handle kick events."""
        await self.handle_leave_events(evt)

    @event.on(InternalEventType.BAN)
    async def handle_ban(self, evt: StateEvent) -> None:
        """Handle ban events."""
        await self.handle_leave_events(evt)

    @event.on(InternalEventType.JOIN)
    async def newjoin(self, evt: StateEvent) -> None:
        if evt.source & SyncStream.STATE:
            return
        else:
            # we only care about join events in rooms in the space
            # this avoids trying to verify users in other rooms the bot might be in,
            # such as public banlist policy rooms
            space_rooms = await self.get_space_roomlist()
            if evt.room_id not in space_rooms:
                return

            try:
                on_banlist = await self.check_if_banned(evt.sender)
            except Exception as e:
                self.log.error(f"Failed to check if {evt.sender} is banned: {e}")
                on_banlist = False
            if on_banlist:
                await self.ban_this_user(evt.sender)
                return
            # passive sync of tracking db
            if evt.room_id == self.config["parent_room"]:
                await self.do_sync()
            # greeting activities
            room_id = str(evt.room_id)
            self.log.debug(f"New join in room {room_id} by {evt.sender}")
            self.log.debug(f"Greeting rooms config: {self.config['greeting_rooms']}")
            self.log.debug(f"Check if human config: {self.config['check_if_human']}")
            self.log.debug(
                f"Verification phrases config: {self.config['verification_phrases']}"
            )

            if room_id in self.config["greeting_rooms"]:
                if on_banlist:
                    return
                greeting_map = self.config["greetings"]
                greeting_name = self.config["greeting_rooms"][room_id]
                nick = self.client.parse_user_id(evt.sender)[0]
                pill = '<a href="https://matrix.to/#/{mxid}">{nick}</a>'.format(
                    mxid=evt.sender, nick=nick
                )
                if greeting_name != "none":
                    greeting = greeting_map[greeting_name].format(user=pill)
                    time.sleep(self.config["welcome_sleep"])
                    await self.client.send_notice(evt.room_id, html=greeting)
                else:
                    pass
                if self.config["notification_room"]:
                    roomnamestate = await self.client.get_state_event(
                        evt.room_id, "m.room.name"
                    )
                    roomname = roomnamestate["name"]
                    notification_message = self.config[
                        "join_notification_message"
                    ].format(user=evt.sender, room=roomname)
                    await self.client.send_notice(
                        self.config["notification_room"], html=notification_message
                    )

            # Human verification logic
            if self.config["check_if_human"] and self.config["verification_phrases"]:
                try:
                    # Check if verification is enabled for this room
                    verification_enabled = False
                    if isinstance(self.config["check_if_human"], bool):
                        verification_enabled = self.config["check_if_human"]
                    elif isinstance(self.config["check_if_human"], list):
                        verification_enabled = (
                            evt.room_id in self.config["check_if_human"]
                        )

                    self.log.debug(
                        f"Verification enabled for room {room_id}: {verification_enabled}"
                    )

                    if not verification_enabled:
                        return

                    # Get room name for greeting
                    roomname = "this room"
                    roomname = await common_utils.get_room_name(
                        self.client, evt.room_id, self.log
                    )

                    # Check if user already has sufficient power level or unlimited power
                    try:
                        # First check if user has unlimited power (creator in modern room versions)
                        if await self.user_has_unlimited_power(evt.sender, evt.room_id):
                            self.log.debug(
                                f"User {evt.sender} has unlimited power in {evt.room_id}, skipping verification"
                            )
                            return

                        power_levels = await self.client.get_state_event(
                            evt.room_id, EventType.ROOM_POWER_LEVELS
                        )
                        user_level = power_levels.get_user_level(evt.sender)
                        events_default = power_levels.events_default
                        events = power_levels.events

                        # Get the required power level for sending messages
                        required_level = events.get(
                            str(EventType.ROOM_MESSAGE), events_default
                        )

                        self.log.debug(
                            f"User {evt.sender} has power level {user_level}, required level is {required_level}"
                        )

                        # If user already has sufficient power level, skip verification
                        if user_level >= required_level:
                            self.log.debug(
                                f"User {evt.sender} already has sufficient power level ({user_level} >= {required_level})"
                            )
                            return
                    except Exception as e:
                        self.log.error(f"Failed to check user power level: {e}")
                        return

                    # Create DM room with name
                    max_retries = 3
                    retry_delay = 1  # seconds
                    last_error = None

                    for attempt in range(max_retries):
                        try:
                            dm_room = await self.client.create_room(
                                preset=RoomCreatePreset.PRIVATE,
                                invitees=[evt.sender],
                                is_direct=True,
                                initial_state=[
                                    {
                                        "type": str(EventType.ROOM_NAME),
                                        "content": {
                                            "name": f"[{roomname}] join verification"
                                        },
                                    }
                                ],
                            )
                            self.log.info(f"Created DM room {dm_room} for {evt.sender}")
                            break
                        except Exception as e:
                            last_error = e
                            if (
                                attempt < max_retries - 1
                            ):  # Don't sleep on the last attempt
                                self.log.warning(
                                    f"Failed to create DM room (attempt {attempt + 1}/{max_retries}): {e}"
                                )
                                await asyncio.sleep(retry_delay)
                            else:
                                self.log.error(
                                    f"Failed to initiate verification process after {max_retries} attempts: {e}"
                                )
                                return

                    # Select random verification phrase
                    verification_phrase = random.choice(
                        self.config["verification_phrases"]
                    )

                    # Store verification state
                    verification_state = {
                        "user": evt.sender,
                        "target_room": evt.room_id,
                        "phrase": verification_phrase,
                        "attempts": self.config["verification_attempts"],
                        "required_level": required_level,
                    }
                    await self.store_verification_state(dm_room, verification_state)

                    # Send greeting
                    greeting = self.config["verification_message"].format(
                        room=roomname, phrase=verification_phrase
                    )
                    await self.client.send_notice(dm_room, html=greeting)
                    self.log.info(
                        f"Started verification process for {evt.sender} in room {room_id} for room {roomname}"
                    )

                except Exception as e:
                    self.log.error(f"Failed to start verification process: {e}")

    @event.on(EventType.ROOM_MESSAGE)
    async def handle_verification(self, evt: MessageEvent) -> None:
        # Ignore messages from the bot itself
        if evt.sender == self.client.mxid:
            return

        state = await self.get_verification_state(evt.room_id)
        if not state:
            # self.log.debug(f"No verification state stored for {evt.room_id}")
            return

        # self.log.debug(f"Checking verification for {evt.sender} in {evt.room_id}")
        user_phrase = evt.content.body.strip().lower()
        expected_phrase = state["phrase"].lower()

        # Remove punctuation and compare
        user_phrase = re.sub(r"[^\w\s]", "", user_phrase)
        expected_phrase = re.sub(r"[^\w\s]", "", expected_phrase)

        if user_phrase == expected_phrase:
            try:
                # confirm user is still in target room
                members = await self.client.get_joined_members(state["target_room"])
                if state["user"] not in members:
                    await self.client.send_notice(
                        evt.room_id,
                        "Looks like you've left the target room. Rejoin to try again.",
                    )
                else:
                    # Update power levels in target room
                    power_levels = await self.client.get_state_event(
                        state["target_room"], EventType.ROOM_POWER_LEVELS
                    )
                    power_levels.users[state["user"]] = state["required_level"]
                    await self.client.send_state_event(
                        state["target_room"], EventType.ROOM_POWER_LEVELS, power_levels
                    )
                    await self.client.send_notice(
                        evt.room_id,
                        "Success! My work here is done. You can leave this room now.",
                    )
            except Exception as e:
                await self.client.send_notice(
                    evt.room_id,
                    f"Something went wrong: {str(e)}. Please report this to the room moderators.",
                )
                if self.config["notification_room"]:
                    await self.client.send_notice(
                        self.config["notification_room"],
                        f"User verification failed for {evt.sender} in room {evt.room_id}, you may need to manually verify them.",
                    )
            finally:
                await self.client.leave_room(evt.room_id)
                await self.delete_verification_state(evt.room_id)
        else:
            state["attempts"] -= 1
            if state["attempts"] <= 0:
                await self.client.send_notice(
                    evt.room_id,
                    "You have run out of attempts. Please contact a room moderator for assistance.",
                )
                if self.config["notification_room"]:
                    await self.client.send_notice(
                        self.config["notification_room"],
                        f"User verification failed for {evt.sender} in room {evt.room_id}, you may need to manually verify them.",
                    )
                await self.client.leave_room(evt.room_id)
                await self.delete_verification_state(evt.room_id)
            else:
                await self.store_verification_state(evt.room_id, state)
                await self.client.send_notice(
                    evt.room_id,
                    f"Phrase does not match, you have {state['attempts']} tries remaining.",
                )

    async def upsert_user_timestamp(self, mxid: str, timestamp: int) -> None:
        """Database-agnostic upsert for user timestamp updates."""
        await database_utils.upsert_user_timestamp(
            self.database, mxid, timestamp, self.log
        )

    @event.on(EventType.ROOM_MESSAGE)
    async def update_message_timestamp(self, evt: MessageEvent) -> None:
        power_levels = await self.client.get_state_event(
            evt.room_id, EventType.ROOM_POWER_LEVELS
        )
        user_level = power_levels.get_user_level(evt.sender)
        # self.log.debug(f"DEBUGDEBUG user {evt.sender} has power level {user_level}")
        if self.flag_message(evt):
            # do we need to redact?
            if (
                not await self.user_permitted(evt.sender)
                and evt.sender != self.client.mxid
                and self.censor_room(evt)
            ):
                try:
                    await self.client.redact(
                        evt.room_id, evt.event_id, reason="message flagged"
                    )
                except Exception as e:
                    self.log.error(f"Flagged message could not be redacted: {e}")
        if evt.content.msgtype in {
            MessageType.TEXT,
            MessageType.NOTICE,
            MessageType.EMOTE,
        }:
            if self.flag_instaban(evt):
                # do we need to redact?
                if (
                    not await self.user_permitted(evt.sender)
                    and evt.sender != self.client.mxid
                    and self.censor_room(evt)
                ):
                    try:
                        await self.client.redact(
                            evt.room_id, evt.event_id, reason="message flagged"
                        )
                    except Exception as e:
                        self.log.error(f"Flagged message could not be redacted: {e}")

                    await self.ban_this_user(evt.sender, all_rooms=True)

        if not self.config_manager.is_message_tracking_enabled():
            pass
        else:
            rooms_to_manage = await self.get_space_roomlist()
            # only attempt to track rooms in the space, ignore any other rooms
            # the bot may happen to be in line banlist policy rooms etc.
            if evt.room_id not in rooms_to_manage:
                return
            else:
                await self.upsert_user_timestamp(evt.sender, evt.timestamp)

    @event.on(EventType.REACTION)
    async def update_reaction_timestamp(self, evt: MessageEvent) -> None:
        if not self.config_manager.is_reaction_tracking_enabled():
            pass
        else:
            rooms_to_manage = await self.get_space_roomlist()
            # only attempt to track rooms in the space, ignore any other rooms
            # the bot may happen to be in line banlist policy rooms etc.
            if evt.room_id not in rooms_to_manage:
                return
            else:
                await self.upsert_user_timestamp(evt.sender, evt.timestamp)

    @command.new("community", help="manage rooms and members of a space")
    async def community(self) -> None:
        pass

    async def check_parent_room(self, evt: MessageEvent) -> bool:
        """Check if parent room is configured and handle the response if not."""
        if not self.config["parent_room"]:
            await evt.reply(
                "No parent room configured. Please use the 'initialize' command to set up your community space first."
            )
            return False
        return True

    @community.subcommand("user", help="manage users in the community")
    @decorators.require_parent_room
    @decorators.require_permission()
    async def user(self, evt: MessageEvent) -> None:
        """Main user command - shows usage by default"""
        await evt.reply(
            "Use !community user <subcommand> to manage users. Available subcommands: bancheck, ban, unban, kick, ignore, unignore, redact"
        )

    @user.subcommand("bancheck", help="check subscribed banlists for a user's mxid")
    @command.argument("mxid", "full matrix ID", required=True)
    async def user_bancheck(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        ban_status = await self.check_if_banned(mxid)
        await evt.reply(f"user on banlist: {ban_status}")

    @user.subcommand(
        "ban", help="kick and ban a specific user from the community and all rooms"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def user_ban(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()

        user = mxid
        msg = await evt.respond("starting the ban...")
        results_map = await self.ban_this_user(user, all_rooms=True)

        results = "the following users were kicked and banned:<p><code>{ban_list}</code></p>the following errors were \
                recorded:<p><code>{error_list}</code></p>".format(
            ban_list=results_map["ban_list"], error_list=results_map["error_list"]
        )
        await evt.respond(results, allow_html=True, edits=msg)

        # sync our database after we've made changes to room memberships
        await self.do_sync()

    @user.subcommand(
        "unban", help="unban a specific user from the community and all rooms"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def user_unban(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()

        user = mxid
        msg = await evt.respond("starting the unban...")
        roomlist = await self.get_space_roomlist()
        # don't forget to kick from the space itself
        roomlist.append(self.config["parent_room"])
        unban_list = {}
        error_list = {}

        unban_list[user] = []
        for room in roomlist:
            try:
                roomname = None
                roomnamestate = await self.client.get_state_event(room, "m.room.name")
                if roomnamestate:
                    roomname = roomnamestate.name
                else:
                    roomname = room

                await self.client.unban_user(room, user)
                unban_list[user].append(roomname)
            except Exception as e:
                error_list[room] = str(e)

        results = "the following users were unbanned:<p><code>{unban_list}</code></p>the following errors were \
                recorded:<p><code>{error_list}</code></p>".format(
            unban_list=unban_list, error_list=error_list
        )
        await evt.respond(results, allow_html=True, edits=msg)

        # sync our database after we've made changes to room memberships
        await self.do_sync()

    @user.subcommand(
        "ignore", help="exclude a specific matrix ID from inactivity tracking"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @decorators.require_parent_room
    @decorators.require_permission()
    @decorators.handle_errors("Failed to ignore user")
    async def user_ignore(self, evt: MessageEvent, mxid: UserID) -> None:
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        Client.parse_user_id(mxid)
        await self.database.execute(
            "UPDATE user_events SET ignore_inactivity = 1 WHERE \
                mxid = $1",
            mxid,
        )
        self.log.info(f"{mxid} set to ignore inactivity")
        await evt.react("✅")

    @user.subcommand(
        "unignore", help="re-enable activity tracking for a specific matrix ID"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @decorators.require_parent_room
    @decorators.require_permission()
    @decorators.handle_errors("Failed to unignore user")
    async def user_unignore(self, evt: MessageEvent, mxid: UserID) -> None:
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        Client.parse_user_id(mxid)
        await self.database.execute(
            "UPDATE user_events SET ignore_inactivity = 0 WHERE \
                mxid = $1",
            mxid,
        )
        self.log.info(f"{mxid} set to track inactivity")
        await evt.react("✅")

    @user.subcommand(
        "redact",
        help="redact messages from a specific user (optionally in a specific room)",
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @command.argument("room", "room ID", required=False)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def user_redact(self, evt: MessageEvent, mxid: UserID, room: str) -> None:
        await evt.mark_read()

        if room:
            if room.startswith("#"):
                try:
                    room_id = await self.client.resolve_room_alias(room)
                    room_id = room_id["room_id"]
                except:
                    evt.reply("i couldn't resolve that alias, sorry")
                    return
            else:
                room_id = room
        else:
            room_id = evt.room_id

        # get list of messages to redact in this room
        messages = await self.get_messages_to_redact(room_id, mxid)
        for msg in messages:
            await self.database.execute(
                "INSERT INTO redaction_tasks (event_id, room_id) VALUES ($1, $2)",
                msg.event_id,
                room_id,
            )
        await evt.respond(f"Queued {len(messages)} messages for redaction in {room_id}")

    @community.subcommand(
        "sync",
        help="update the activity tracker with the current space members \
            in case they are missing",
    )
    @decorators.require_parent_room
    @decorators.require_permission()
    async def sync_space_members(self, evt: MessageEvent) -> None:

        # Power level sync is now handled through parent room inheritance
        # Users should set power levels directly in the parent room

        if not self.config["track_users"]:
            await evt.respond("user tracking is disabled")
            return

        results = await self.do_sync()

        added_str = "<br />".join(results["added"])
        dropped_str = "<br />".join(results["dropped"])
        await evt.respond(
            f"Added: {added_str}<br /><br />Dropped: {dropped_str}", allow_html=True
        )

    @community.subcommand(
        "report", help="generate reports of user activity and inactivity"
    )
    @decorators.require_parent_room
    @decorators.require_permission()
    async def report(self, evt: MessageEvent) -> None:
        """Main report command - shows full report by default"""
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(
            f"<p><b>Users inactive for between {self.config['warn_threshold_days']} and \
                {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['warn_inactive'])} <br /></p>\
                <p><b>Users inactive for at least {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['kick_inactive'])} <br /></p> \
                <p><b>Ignored users:</b><br /> \
                {'<br />'.join(report['ignored'])}</p>",
            allow_html=True,
        )

    @report.subcommand("all", help="generate a full report of all user activity status")
    @decorators.require_parent_room
    @decorators.require_permission()
    async def report_all(self, evt: MessageEvent) -> None:
        """Report all user activity status - same as main report command"""
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(
            f"<p><b>Users inactive for between {self.config['warn_threshold_days']} and \
                {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['warn_inactive'])} <br /></p>\
                <p><b>Users inactive for at least {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['kick_inactive'])} <br /></p> \
                <p><b>Ignored users:</b><br /> \
                {'<br />'.join(report['ignored'])}</p>",
            allow_html=True,
        )

    @report.subcommand(
        "inactive", help="generate a list of users who have been inactive"
    )
    @decorators.require_parent_room
    @decorators.require_permission()
    async def report_inactive(self, evt: MessageEvent) -> None:
        """Report users who are inactive but not yet at kick threshold"""
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(
            f"<p><b>Users inactive for between {self.config['warn_threshold_days']} and \
                {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['warn_inactive'])} <br /></p>",
            allow_html=True,
        )

    @report.subcommand(
        "purgable",
        help="generate a list of users that would be kicked with the purge command",
    )
    @decorators.require_parent_room
    @decorators.require_permission()
    async def report_purgable(self, evt: MessageEvent) -> None:
        """Report users who are inactive long enough to be purged"""
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(
            f"<p><b>Users inactive for at least {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['kick_inactive'])} <br /></p>",
            allow_html=True,
        )

    @report.subcommand(
        "ignored", help="generate a list of users that have activity tracking disabled"
    )
    @decorators.require_parent_room
    @decorators.require_permission()
    async def report_ignored(self, evt: MessageEvent) -> None:
        """Report users who are ignored for activity tracking"""
        if not self.config_manager.is_tracking_enabled():
            await evt.reply("user tracking is disabled")
            return

        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(
            f"<p><b>Ignored users:</b><br /> \
                {'<br />'.join(report['ignored'])}</p>",
            allow_html=True,
        )

    @community.subcommand("purge", help="kick users for excessive inactivity")
    @decorators.require_parent_room
    @decorators.require_permission()
    async def kick_users(self, evt: MessageEvent) -> None:
        await evt.mark_read()

        msg = await evt.respond("starting the purge...")
        report = await self.generate_report()
        purgeable = report["kick_inactive"]
        roomlist = await self.get_space_roomlist()
        # don't forget to kick from the space itself
        roomlist.append(self.config["parent_room"])
        purge_list = {}
        error_list = {}

        for user in purgeable:
            purge_list[user] = []
            for room in roomlist:
                try:
                    roomname = None
                    roomnamestate = await self.client.get_state_event(
                        room, "m.room.name"
                    )
                    roomname = roomnamestate["name"]

                    await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                    await self.client.kick_user(room, user, reason="inactivity")
                    if roomname:
                        purge_list[user].append(roomname)
                    else:
                        purge_list[user].append(room)
                    time.sleep("sleep")
                except MNotFound:
                    pass
                except Exception as e:
                    self.log.warning(e)
                    error_list[user] = []
                    error_list[user].append(roomname or room)

        results = "the following users were purged:<p><code>{purge_list}</code></p>the following errors were \
                recorded:<p><code>{error_list}</code></p>".format(
            purge_list=purge_list, error_list=error_list
        )
        await evt.respond(results, allow_html=True, edits=msg)

        # sync our database after we've made changes to room memberships
        await self.do_sync()

    @user.subcommand(
        "kick", help="kick a specific user from the community and all rooms"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def user_kick(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()

        user = mxid
        msg = await evt.respond("starting the kick...")
        roomlist = await self.get_space_roomlist()
        # don't forget to kick from the space itself
        roomlist.append(self.config["parent_room"])
        kick_list = {}
        error_list = {}

        kick_list[user] = []
        for room in roomlist:
            try:
                roomname = None
                roomnamestate = await self.client.get_state_event(room, "m.room.name")
                roomname = roomnamestate["name"]

                await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                await self.client.kick_user(room, user, reason="kicked")
                if roomname:
                    kick_list[user].append(roomname)
                else:
                    kick_list[user].append(room)
                time.sleep(self.config["sleep"])
            except MNotFound:
                pass
            except Exception as e:
                self.log.warning(e)
                error_list[user] = []
                error_list[user].append(roomname or room)

        results = "the following users were kicked:<p><code>{kick_list}</code></p>the following errors were \
                recorded:<p><code>{error_list}</code></p>".format(
            kick_list=kick_list, error_list=error_list
        )
        await evt.respond(results, allow_html=True, edits=msg)

        # sync our database after we've made changes to room memberships
        await self.do_sync()

    async def create_room(
        self,
        roomname: str,
        evt: MessageEvent = None,
        power_level_override: Optional[PowerLevelStateEventContent] = None,
        creation_content: Optional[dict] = None,
        invitees: Optional[list[str]] = None,
    ) -> tuple[str, str] | None:
        """Create a new room and add it to the parent space.

        Args:
            roomname: The name for the new room
            evt: Optional MessageEvent for progress updates. If provided, will send status messages.
            power_level_override: Optional power levels to use. If not provided, will try to get from parent room.
            creation_content: Optional creation content to use when creating the room.
            invitees: Optional list of users to invite. If not provided, uses config invitees.

        Returns:
            tuple: (room_id, room_alias) if successful, None if failed
        """
        mymsg = None
        try:
            # Validate and process room creation parameters
            (
                sanitized_name,
                force_encryption,
                force_unencryption,
                error_msg,
                cleaned_roomname,
            ) = await room_creation_utils.validate_room_creation_params(
                roomname, self.config, evt
            )
            if error_msg:
                self.log.error(error_msg)
                if evt:
                    await evt.respond(error_msg)
                return None

            # Prepare room creation data
            alias_localpart, server, room_invitees, parent_room = (
                await room_creation_utils.prepare_room_creation_data(
                    sanitized_name, self.config, self.client, invitees
                )
            )

            # Validate that the alias is available
            is_available = await self.validate_room_alias(alias_localpart, server)
            if not is_available:
                error_msg = f"Room alias #{alias_localpart}:{server} already exists. Cannot create room."
                self.log.error(error_msg)
                if evt:
                    await evt.respond(error_msg)
                return None

            # Prepare power levels
            try:
                power_levels = await room_creation_utils.prepare_power_levels(
                    self.client, self.config, parent_room, power_level_override
                )
                self.log.info(f"Power levels prepared successfully: {power_levels}")
            except Exception as e:
                self.log.error(f"Failed to prepare power levels: {e}")
                raise

            # Adjust power levels for modern rooms
            power_levels = room_creation_utils.adjust_power_levels_for_modern_rooms(
                power_levels, self.config["room_version"]
            )

            if (
                self.is_modern_room_version(self.config["room_version"])
                and power_levels
            ):
                self.log.info(
                    f"Modern room version {self.config['room_version']} detected - removing bot from power levels"
                )
                if power_levels.users:
                    power_levels.users.pop(self.client.mxid, None)

            if evt:
                mymsg = await evt.respond(
                    f"creating {alias_localpart} with room version {self.config['room_version']}, give me a minute..."
                )

            # Prepare initial state events
            initial_state = room_creation_utils.prepare_initial_state(
                self.config,
                parent_room,
                server,
                force_encryption,
                force_unencryption,
                creation_content,
            )

            # Create the room
            self.log.info(
                f"Creating room with room_version={self.config['room_version']}"
            )
            if power_levels:
                self.log.info(
                    f"Power level override users: {list(power_levels.users.keys()) if power_levels.users else 'None'}"
                )
            else:
                self.log.info("No power level override")

            try:
                room_id = await self.client.create_room(
                    alias_localpart=alias_localpart,
                    name=cleaned_roomname,
                    invitees=room_invitees,
                    initial_state=initial_state,
                    power_level_override=power_levels,
                    creation_content=creation_content,
                    room_version=self.config["room_version"],
                )
                self.log.info(f"Room created successfully: {room_id}")
            except Exception as e:
                self.log.error(f"Failed to create room via Matrix API: {e}")
                raise

            # Verify room creation
            await room_creation_utils.verify_room_creation(
                self.client, room_id, self.config["room_version"], self.log
            )

            # Add room to space
            await room_creation_utils.add_room_to_space(
                self.client, parent_room, room_id, server, self.config["sleep"]
            )

            if evt:
                await evt.respond(
                    f"<a href='https://matrix.to/#/#{alias_localpart}:{server}'>#{alias_localpart}:{server}</a> has been created and added to the space.",
                    edits=mymsg,
                    allow_html=True,
                )

            return room_id, f"#{alias_localpart}:{server}"

        except Exception as e:
            error_msg = f"Failed to create room: {e}"
            self.log.error(error_msg)
            if evt and mymsg:
                await evt.respond(error_msg, edits=mymsg)
            elif evt:
                await evt.respond(error_msg)
            return None

    @community.subcommand("room", help="manage rooms in the community")
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room(self, evt: MessageEvent) -> None:
        """Main room command - shows usage by default"""
        await evt.reply(
            "Use !community room <subcommand> to manage rooms. Available subcommands: create, archive, replace, guests, id, version, setpower, enable-verification"
        )

    @room.subcommand(
        "create",
        help="create a new room titled <roomname> and add it to the parent space. \
                          optionally include `--encrypted` or `--unencrypted` to force regardless of the default settings.",
    )
    @command.argument("roomname", pass_raw=True, required=True)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room_create(self, evt: MessageEvent, roomname: str) -> None:
        if (roomname == "help") or len(roomname) == 0:
            await evt.reply(
                'pass me a room name (like "cool topic") and i will create it and add it to the space. \
                            use `--encrypted` or `--unencrypted` to ensure encryption is enabled/disabled at creation time even if that isnt my default \
                            setting.'
            )
            return

        # Check if community slug is configured
        if self.config["use_community_slug"] and not self.config["community_slug"]:
            await evt.reply(
                "No community slug configured. Please run initialize command first."
            )
            return

        # Validate the room alias before creating
        is_valid, conflicting_aliases = await self.validate_room_aliases(
            [roomname], evt
        )
        if not is_valid:
            await evt.reply(
                f"Cannot create room: {conflicting_aliases[0]} already exists."
            )
            return

        result = await self.create_room(roomname, evt)
        if not result:
            return  # Error already logged and reported to user by create_room

    @room.subcommand("archive", help="archive a room")
    @command.argument("room", required=False)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room_archive(self, evt: MessageEvent, room: str) -> None:
        await evt.mark_read()

        if not room:
            room_id = evt.room_id
            self.log.debug(f"DEBUG room we are archiving is {room_id}")
        elif room and room.startswith("#"):
            try:
                self.log.debug(f"DEBUG trying to resolve alias {room}")
                room_id = await self.client.resolve_room_alias(room)
                room_id = room_id["room_id"]
                self.log.debug(f"DEBUG room we are archiving is {room_id}")
            except Exception as e:
                await evt.reply("i couldn't resolve that alias, sorry")
                self.log.error(f"error resolving alias {room}: {e}")
                return
        elif room and room.startswith("!"):
            room_id = room
            self.log.debug(f"DEBUG room we are archiving is {room_id}")
        else:
            await evt.reply("i don't recognize that room, sorry")
            return

        success = await self.do_archive_room(room_id, evt)

        # Only try to respond if we're not archiving the room we're in
        if success and room_id != evt.room_id:
            await evt.respond("Room has been archived.")

    @room.subcommand("replace", help="replace a room with a new one")
    @command.argument("room", required=False)
    @decorators.require_parent_room
    @decorators.require_permission(min_level=100)
    async def room_replace(self, evt: MessageEvent, room: str) -> None:
        self.log.info(f"=== REPLACEROOM COMMAND STARTED ===")
        self.log.info(f"Command arguments: room='{room}', evt.room_id='{evt.room_id}'")

        await evt.mark_read()

        if not room:
            room = evt.room_id
        # first we need to get relevant room state of the room we want to replace
        # this includes the room name, alias, and join rules
        if room.startswith("#"):
            room_id = await self.client.resolve_room_alias(room)
            room_id = room_id["room_id"]
            self.log.info(f"Resolved alias '{room}' to room ID: {room_id}")
        else:
            room_id = room
            self.log.info(f"Using direct room ID: {room_id}")

        # Check bot permissions in the old room
        self.log.info(f"=== CHECKING BOT PERMISSIONS ===")
        has_perms, error_msg, _ = await self.check_bot_permissions(
            room_id, evt, ["state", "tombstone", "power_levels"]
        )
        self.log.info(
            f"Bot permissions check result: has_perms={has_perms}, error_msg='{error_msg}'"
        )
        if not has_perms:
            await evt.respond(f"Cannot replace room: {error_msg}")
            self.log.info("Bot permissions check failed, returning")
            return

        # Get the room name from the state event
        room_name = None
        try:
            room_name_event = await self.client.get_state_event(
                room_id, EventType.ROOM_NAME
            )
            room_name = room_name_event.name
            self.log.info(f"Retrieved room name: '{room_name}'")
        except Exception as e:
            self.log.warning(f"Failed to get room name: {e}")
            # room_name remains None

        # get the room topic from the state event
        room_topic = None
        try:
            room_topic_event = await self.client.get_state_event(
                room_id, EventType.ROOM_TOPIC
            )
            room_topic = room_topic_event.topic
        except Exception as e:
            self.log.warning(f"Failed to get room topic: {e}")
            # room_topic remains None

        # Check if the room being replaced is a space
        is_space = False
        self.log.info(f"=== ABOUT TO START SPACE DETECTION ===")
        self.log.info(f"=== SPACE DETECTION DEBUG START ===")
        self.log.info(f"Room ID being checked: {room_id}")
        self.log.info(f"EventType module: {EventType}")
        self.log.info(
            f"EventType.ROOM_CREATE exists: {hasattr(EventType, 'ROOM_CREATE')}"
        )
        if hasattr(EventType, "ROOM_CREATE"):
            self.log.info(
                f"EventType.ROOM_CREATE value: {getattr(EventType, 'ROOM_CREATE')}"
            )
        else:
            self.log.warning("EventType.ROOM_CREATE does not exist!")

        try:
            # Get the room creation event to check if it's a space
            state_events = await self.client.get_state(room_id)
            self.log.info(
                f"Retrieved {len(state_events)} state events for space detection"
            )

            # Log all event types for debugging
            event_types = [event.type for event in state_events]
            self.log.info(f"Event types found: {event_types}")

            # Debug EventType.ROOM_CREATE constant
            self.log.info(f"EventType.ROOM_CREATE value: {EventType.ROOM_CREATE}")
            self.log.info(f"EventType.ROOM_CREATE type: {type(EventType.ROOM_CREATE)}")

            # Also try string comparison as fallback
            room_create_string = "m.room.create"
            self.log.info(f"String comparison value: {room_create_string}")

            # Try to find the room creation event using multiple methods
            room_create_event = None

            for i, event in enumerate(state_events):
                self.log.info(
                    f"Event {i}: type={event.type} (type: {type(event.type)})"
                )

                # Try multiple comparison methods
                if (
                    hasattr(EventType, "ROOM_CREATE")
                    and event.type == EventType.ROOM_CREATE
                ):
                    self.log.info(f"✓ Matched EventType.ROOM_CREATE")
                    room_create_event = event
                    break
                elif str(event.type) == room_create_string:
                    self.log.info(f"✓ Matched string comparison 'm.room.create'")
                    room_create_event = event
                    break
                elif event.type == "m.room.create":
                    self.log.info(f"✓ Matched direct string comparison")
                    room_create_event = event
                    break
                else:
                    self.log.info(f"✗ No match for event {i}")

            # Now process the room creation event if found
            if room_create_event:
                space_type = room_create_event.content.get("type")
                self.log.info(f"Found ROOM_CREATE event with type: {space_type}")
                self.log.info(f"Full ROOM_CREATE content: {room_create_event.content}")
                is_space = space_type == "m.space"
                self.log.info(f"Space detection result: {is_space}")
            else:
                self.log.warning("No ROOM_CREATE event found using any method")

            if is_space:
                self.log.info(
                    f"✓ FINAL RESULT: Room {room_id} IS a space - will create new space"
                )
            else:
                self.log.info(
                    f"✗ FINAL RESULT: Room {room_id} is NOT a space - will create regular room"
                )

        except Exception as e:
            self.log.error(f"❌ ERROR during space detection: {e}")
            import traceback

            self.log.error(f"Traceback: {traceback.format_exc()}")
            # Assume it's not a space if we can't determine
            is_space = False

        self.log.info(f"=== SPACE DETECTION DEBUG END - is_space={is_space} ===")

        # Get list of aliases to transfer while removing them from the old room
        aliases_to_transfer = await self.remove_room_aliases(room_id, evt)

        # Check if community slug is configured
        if self.config["use_community_slug"] and not self.config["community_slug"]:
            await evt.respond(
                "No community slug configured. Please run initialize command first."
            )
            return

        # Inform user about what type of room is being replaced
        if not room_name:
            room_name = f"Room {room_id[:8]}..."  # Fallback name
            self.log.warning(f"Using fallback room name: {room_name}")

        self.log.info(
            f"Final decision - is_space: {is_space}, room_name: '{room_name}'"
        )
        self.log.info(f"About to send user message - is_space: {is_space}")

        if is_space:
            await evt.respond(f"Replacing space '{room_name}' with a new space...")
            self.log.info(f"✓ Sent 'Replacing space' message to user")
        else:
            await evt.respond(f"Replacing room '{room_name}' with a new room...")
            self.log.info(f"✗ Sent 'Replacing room' message to user")

        # Validate that the new room alias is available
        is_valid, conflicting_aliases = await self.validate_room_aliases(
            [room_name], evt
        )
        if not is_valid:
            await evt.respond(
                f"Cannot replace room: {conflicting_aliases[0]} already exists."
            )
            return

        # Now we can start the process of replacing the room
        # First we need to create the new room. this will create the initial alias,
        # as well as bot defaults such as power levels, initial invitations, encryption,
        # and space membership
        if is_space:
            # Create a new space instead of a regular room
            # For spaces, we need to pass power_level_override to ensure proper creation
            # Get power levels from the old space to use as a template
            try:
                old_power_levels = await self.client.get_state_event(
                    room_id, EventType.ROOM_POWER_LEVELS
                )
                self.log.info(
                    f"Using user power levels from old space for new space creation"
                )

                # Create new power levels with server defaults, not copying all permissions from old space
                power_levels = PowerLevelStateEventContent()

                # Copy only user power levels from old space, not the entire permission set
                if old_power_levels.users:
                    user_power_levels = old_power_levels.users.copy()
                    # Ensure bot has highest power
                    user_power_levels[self.client.mxid] = 1000
                    power_levels.users = user_power_levels
                else:
                    power_levels.users = {
                        self.client.mxid: 1000,  # Bot gets highest power
                    }

                # Set explicit config values
                power_levels.invite = self.config["invite_power_level"]

                # For other permissions, let the server use its defaults instead of copying from old space
                # This prevents issues like only admins being able to post messages
                self.log.info(
                    f"Using user power levels from old space but server defaults for other permissions"
                )
                power_level_override = power_levels

                # remove the bot's explicit power level for modern room versions
                # since creators have unlimited power in modern rooms
                if self.is_modern_room_version(self.config["room_version"]):
                    if power_level_override.users:
                        power_level_override.users.pop(self.client.mxid, None)
                        self.log.info(f"Removed bot since they are creator")
            except Exception as e:
                self.log.warning(
                    f"Could not get power levels from old space, using defaults: {e}"
                )
                power_level_override = None

            self.log.info(
                f"Calling create_space with room_name='{room_name}', power_level_override={power_level_override is not None}"
            )
            new_room_id, new_room_alias = await self.create_space(
                room_name, evt, power_level_override
            )
            self.log.info(
                f"create_space returned: room_id={new_room_id}, alias={new_room_alias}"
            )
        else:
            # Create a regular room
            self.log.info(f"Calling create_room with room_name='{room_name}'")
            new_room_id, new_room_alias = await self.create_room(room_name, evt)
            self.log.info(
                f"create_room returned: room_id={new_room_id}, alias={new_room_alias}"
            )

        if not new_room_id:
            await evt.respond("Failed to create new room")
            return

        # Ensure the new space is NOT added to the old space as a child room
        if is_space:
            try:
                # Check if the old space has any m.space.parent events pointing to it
                # and ensure the new space doesn't get added as a child
                old_space_parent_events = []
                state_events = await self.client.get_state(room_id)
                for event in state_events:
                    if event.type == EventType.SPACE_PARENT:
                        old_space_parent_events.append(event.state_key)

                if old_space_parent_events:
                    self.log.info(
                        f"Old space has {len(old_space_parent_events)} parent space references - ensuring new space is not added as child"
                    )
                    await evt.respond(
                        f"Note: Old space has {len(old_space_parent_events)} parent space references - new space will be independent"
                    )

                # Also check if the old space is a child of the community parent space
                # and ensure the new space doesn't automatically inherit that relationship
                if room_id == self.config.get("parent_room"):
                    self.log.info(
                        "Old space is the community parent space - new space will be independent"
                    )
                    await evt.respond(
                        "Note: Old space is the community parent space - new space will be independent and may need manual configuration"
                    )
            except Exception as e:
                self.log.warning(f"Could not check old space parent references: {e}")

        # Check bot permissions in the new room
        has_perms, error_msg, _ = await self.check_bot_permissions(
            new_room_id, evt, ["state", "tombstone", "power_levels"]
        )
        if not has_perms:
            await evt.respond(
                f"Created new room but cannot complete replacement: {error_msg}"
            )
            return

        # Transfer the aliases to the new room/space
        if aliases_to_transfer:
            await evt.respond(
                f"Transferring {len(aliases_to_transfer)} aliases to new {'space' if is_space else 'room'}..."
            )

            for alias in aliases_to_transfer:
                localpart = alias.split(":")[0][1:]  # Remove # and get localpart
                server = alias.split(":")[1]
                try:
                    await self.client.add_room_alias(new_room_id, localpart)
                    self.log.info(
                        f"Successfully transferred alias {alias} to new {'space' if is_space else 'room'} {new_room_id}"
                    )
                except Exception as e:
                    # If transfer failed, try to create a modified alias
                    modified_alias = f"{localpart}NEW"
                    try:
                        await self.client.add_room_alias(new_room_id, modified_alias)
                        self.log.info(
                            f"Successfully transferred modified alias {modified_alias} to new {'space' if is_space else 'room'} {new_room_id}"
                        )
                    except Exception as e2:
                        self.log.error(
                            f"Failed to transfer modified alias {modified_alias}: {e2}"
                        )

            await evt.respond(
                f"Successfully transferred {len(aliases_to_transfer)} aliases to new {'space' if is_space else 'room'}"
            )
        else:
            await evt.respond("No aliases to transfer")

        # Get the room avatar from the old room/space
        try:
            old_room_avatar = await self.client.get_state_event(
                room_id, EventType.ROOM_AVATAR
            )
            if old_room_avatar and old_room_avatar.url:
                # Set the same avatar in the new room/space
                await self.client.send_state_event(
                    new_room_id, EventType.ROOM_AVATAR, {"url": old_room_avatar.url}
                )
                self.log.info(
                    f"Successfully copied {'space' if is_space else 'room'} avatar to new {'space' if is_space else 'room'} {new_room_id}"
                )
                await evt.respond(
                    f"Copied avatar to new {'space' if is_space else 'room'}"
                )
        except Exception as e:
            self.log.error(
                f"Failed to copy {'space' if is_space else 'room'} avatar to new {'space' if is_space else 'room'}: {e}"
            )
            # await evt.respond(f"Failed to copy {'space' if is_space else 'room'} avatar to new {'space' if is_space else 'room'}: {e}")

        # Set the room topic in the new room/space
        if room_topic:
            try:
                await self.client.send_state_event(
                    new_room_id, EventType.ROOM_TOPIC, {"topic": room_topic}
                )
                self.log.info(
                    f"Successfully copied {'space' if is_space else 'room'} topic to new {'space' if is_space else 'room'} {new_room_id}"
                )
                await evt.respond(
                    f"Copied topic to new {'space' if is_space else 'room'}"
                )
            except Exception as e:
                self.log.error(
                    f"Failed to copy {'space' if is_space else 'room'} topic to new {'space' if is_space else 'room'}: {e}"
                )
                # await evt.respond(f"Failed to copy {'space' if is_space else 'room'} topic to new {'space' if is_space else 'room'}: {e}")
        else:
            await evt.respond("No topic to copy")

        # Archive the old room/space with a pointer to the new room/space
        await evt.respond(f"Archiving old {'space' if is_space else 'room'}...")
        success = await self.do_archive_room(room_id, evt, new_room_id)
        if not success:
            await evt.respond(
                f"Failed to archive old {'space' if is_space else 'room'}, but new {'space' if is_space else 'room'} has been created"
            )
        else:
            await evt.respond(
                f"Successfully archived old {'space' if is_space else 'room'}"
            )

        # If we're replacing a space, we need to handle child room relationships
        if is_space:
            try:
                # Get all child rooms from the old space
                old_child_rooms = []
                state_events = await self.client.get_state(room_id)
                for event in state_events:
                    if event.type == EventType.SPACE_CHILD:
                        old_child_rooms.append(event.state_key)

                if old_child_rooms:
                    self.log.info(
                        f"Found {len(old_child_rooms)} child rooms in old space"
                    )
                    await evt.respond(
                        f"Migrating {len(old_child_rooms)} child rooms from old space to new space..."
                    )

                    # Update child rooms to point to the new space
                    for child_room_id in old_child_rooms:
                        try:
                            # Remove old space parent reference
                            await self.client.send_state_event(
                                child_room_id,
                                EventType.SPACE_PARENT,
                                {},  # Empty content removes the state
                                state_key=room_id,
                            )
                            # Add new space parent reference
                            server = self.client.parse_user_id(self.client.mxid)[1]
                            await self.client.send_state_event(
                                child_room_id,
                                EventType.SPACE_PARENT,
                                {"via": [server], "canonical": True},
                                state_key=new_room_id,
                            )
                            # Update space child reference
                            await self.client.send_state_event(
                                new_room_id,
                                EventType.SPACE_CHILD,
                                {"via": [server], "suggested": False},
                                state_key=child_room_id,
                            )
                            self.log.info(
                                f"Updated child room {child_room_id} to point to new space"
                            )
                            await asyncio.sleep(self.config["sleep"])
                        except Exception as e:
                            self.log.error(
                                f"Failed to update child room {child_room_id}: {e}"
                            )

                    await evt.respond(
                        f"Successfully migrated {len(old_child_rooms)} child rooms to new space"
                    )
                else:
                    await evt.respond("No child rooms found in old space")
            except Exception as e:
                self.log.error(f"Failed to handle child room relationships: {e}")
                await evt.respond(
                    f"Warning: Failed to handle child room relationships: {e}"
                )

        # update instances of the old room id in any config values that use it
        config_keys = [
            "parent_room",
            "notification_room",
            "censor",
            "check_if_human",
            "banlists",
            "greeting_rooms",
        ]

        for key in config_keys:
            value = self.config[key]
            if isinstance(value, str):
                if value == room_id:
                    self.config[key] = new_room_id
            elif isinstance(value, list):
                # Handle lists that might contain room IDs
                if room_id in value:
                    self.config[key] = [
                        new_room_id if x == room_id else x for x in value
                    ]
            elif isinstance(value, dict):
                # Handle dictionaries that might use room IDs as keys
                if room_id in value:
                    self.config[key][new_room_id] = self.config[key].pop(room_id)
                # Also check if any values in the dict are room IDs
                for dict_key, dict_value in value.items():
                    if dict_value == room_id:
                        self.config[key][dict_key] = new_room_id

        # Save the updated config
        self.config.save()

        # Final success message
        if is_space:
            await evt.respond(
                f"✅ Space replacement completed successfully!\n"
                f"New space: {new_room_alias}\n"
                f"Old space has been archived with a pointer to the new space."
            )
        else:
            await evt.respond(
                f"✅ Room replacement completed successfully!\n"
                f"New room: {new_room_alias}\n"
                f"Old room has been archived with a pointer to the new room."
            )

    @room.subcommand(
        "guests",
        help="generate a list of members in a room who are not members of the parent space",
    )
    @command.argument("room", required=False)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room_guests(self, evt: MessageEvent, room: str) -> None:
        space_members_obj = await self.client.get_joined_members(
            self.config["parent_room"]
        )
        space_members_list = space_members_obj.keys()
        room_id = None
        if room:
            if room.startswith("#"):
                try:
                    thatroom_id = await self.client.resolve_room_alias(room)
                    room_id = thatroom_id["room_id"]
                except:
                    evt.reply("i don't recognize that room, sorry")
                    return
            else:
                room_id = room
        else:
            room_id = evt.room_id
        room_members_obj = await self.client.get_joined_members(room_id)
        room_members_list = room_members_obj.keys()

        # find the non-space members in the room member list
        try:
            guest_list = set(room_members_list) - set(space_members_list)
            if len(guest_list) == 0:
                guest_list = ["None"]
            await evt.reply(
                f"<b>Guests in this room are:</b><br /> \
                    {'<br />'.join(guest_list)}",
                allow_html=True,
            )
        except Exception as e:
            await evt.respond(f"something went wrong: {e}")

    @room.subcommand("id", help="return the matrix room ID of this, or a given, room")
    @command.argument("room", required=False)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room_id(self, evt: MessageEvent, room: str) -> None:
        room_id = None
        if room:
            if room.startswith("#"):
                try:
                    thatroom_id = await self.client.resolve_room_alias(room)
                    room_id = thatroom_id["room_id"]
                except:
                    evt.reply("i don't recognize that room, sorry")
                    return
            else:
                room_id = room
        else:
            room_id = evt.room_id
        try:
            await evt.reply(f"Room ID is: {room_id}")
        except Exception as e:
            await evt.respond(f"something went wrong: {e}")

    @room.subcommand(
        "version", help="return the room version and creators of this, or a given, room"
    )
    @command.argument("room", required=False)
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room_version(self, evt: MessageEvent, room: str) -> None:
        room_id = None
        if room:
            if room.startswith("#"):
                try:
                    thatroom_id = await self.client.resolve_room_alias(room)
                    room_id = thatroom_id["room_id"]
                except:
                    evt.reply("i don't recognize that room, sorry")
                    return
            else:
                room_id = room
        else:
            room_id = evt.room_id

        try:
            room_version, creators = await self.get_room_version_and_creators(room_id)

            # Get room name if available
            room_name = room_id
            try:
                room_name_event = await self.client.get_state_event(
                    room_id, EventType.ROOM_NAME
                )
                room_name = room_name_event.name
            except:
                pass

            response = f"<b>Room:</b> {room_name}<br />"
            response += f"<b>Room ID:</b> {room_id}<br />"
            response += f"<b>Room Version:</b> {room_version}<br />"

            if creators:
                response += f"<b>Creators:</b> {', '.join(creators)}<br />"
                if self.is_modern_room_version(room_version):
                    response += f"<br />ℹ️ <b>Note:</b> This room uses version {room_version}, which means creators have unlimited power and cannot be restricted by power levels."
            else:
                response += "<b>Creators:</b> None found<br />"

            await evt.reply(response, allow_html=True)
        except Exception as e:
            await evt.respond(f"something went wrong: {e}")

    @room.subcommand(
        "setpower",
        help="sync user power levels from parent room to all child rooms. this will override existing user power levels in child rooms!",
    )
    @command.argument("target_room", required=False)
    @decorators.require_parent_room
    @decorators.require_permission(min_level=100)
    async def room_setpower(self, evt: MessageEvent, target_room: str = None) -> None:
        await evt.mark_read()

        if target_room:
            roomlist = [target_room]
            target_msg = target_room
        else:
            roomlist = await self.get_space_roomlist()
            target_msg = "space rooms"

        msg = await evt.respond(
            f"Syncing power levels from parent room to {target_msg}..."
        )

        success_list = []
        skipped_list = []
        error_list = []

        try:
            # Get parent room power levels and version to use as source of truth
            parent_power_levels = await self.client.get_state_event(
                self.config["parent_room"], EventType.ROOM_POWER_LEVELS
            )
            parent_version, parent_creators = await self.get_room_version_and_creators(
                self.config["parent_room"]
            )

            self.log.info(f"Parent room version: {parent_version}")
            self.log.info(f"Parent room creators: {parent_creators}")
            self.log.info(f"Bot MXID: {self.client.mxid}")
            self.log.info(
                f"Bot is creator in parent: {self.client.mxid in parent_creators}"
            )

            user_power_levels = parent_power_levels.users.copy()

            # Handle bot's power level based on room versions and actual creator status
            if self.is_modern_room_version(parent_version):
                # In modern parent rooms, check if bot is actually a creator
                if self.client.mxid in parent_creators:
                    # Bot is a creator, remove from power levels to prevent errors
                    user_power_levels.pop(self.client.mxid, None)
                    self.log.info(
                        f"Parent room is modern (v{parent_version}), bot is creator and has unlimited power"
                    )
                else:
                    # Bot is not a creator, set appropriate power level
                    user_power_levels[self.client.mxid] = 1000
                    self.log.info(
                        f"Parent room is modern (v{parent_version}), bot is not creator, power level set to 1000"
                    )
            else:
                # In legacy parent rooms, ensure bot has highest power level
                user_power_levels[self.client.mxid] = 1000
                self.log.info(
                    f"Parent room is legacy (v{parent_version}), bot power level set to 1000"
                )

            for room in roomlist:
                try:
                    roomname = None
                    try:
                        roomnamestate = await self.client.get_state_event(
                            room, "m.room.name"
                        )
                        roomname = roomnamestate["name"]
                    except Exception as e:
                        self.log.warning(f"Could not get room name for {room}: {e}")

                    # Skip rooms that are protected by verification, unless its the only target room,
                    # in which case we have explicitly asked to set power levels in that room
                    if len(roomlist) > 1 and (
                        (
                            isinstance(self.config["check_if_human"], bool)
                            and self.config["check_if_human"]
                        )
                        or (
                            isinstance(self.config["check_if_human"], list)
                            and room in self.config["check_if_human"]
                        )
                    ):
                        self.log.info(
                            f"Skipping {roomname or room} as it requires human verification. You can explicitly run this command for this room to override."
                        )
                        skipped_list.append(roomname or room)
                        continue

                    # Get the room's power levels object and version info
                    room_power_levels = await self.client.get_state_event(
                        room, EventType.ROOM_POWER_LEVELS
                    )
                    room_version, room_creators = (
                        await self.get_room_version_and_creators(room)
                    )

                    self.log.info(
                        f"Processing room {roomname or room} (v{room_version}) - Parent is v{parent_version}"
                    )

                    # Handle power level mapping based on room version differences
                    if self.is_modern_room_version(room_version):
                        # Target room is modern (v12+) - creators have unlimited power
                        self.log.info(
                            f"Target room {roomname or room} is modern - preserving creator power levels"
                        )

                        # Filter out any users who are creators in the target room
                        filtered_user_power_levels = {}
                        for user, level in user_power_levels.items():
                            if user not in room_creators:
                                filtered_user_power_levels[user] = level
                            else:
                                self.log.info(
                                    f"Skipping power level for creator {user} in modern room {roomname or room}"
                                )

                        # Preserve existing power levels for special cases (like verification rooms)
                        # Only update non-creator users to avoid conflicts
                        existing_users = set(room_power_levels.users.keys())
                        creators_set = set(room_creators)
                        special_users = existing_users - creators_set

                        # Keep existing power levels for special users unless explicitly overridden
                        for user in special_users:
                            if user not in filtered_user_power_levels:
                                filtered_user_power_levels[user] = (
                                    room_power_levels.users[user]
                                )
                                self.log.info(
                                    f"Preserving existing power level for special user {user} in {roomname or room}"
                                )

                        # Handle bot power level in modern target room
                        if self.client.mxid in room_creators:
                            # Bot is creator in target room - don't set power level
                            self.log.info(
                                f"Bot is creator in modern target room {roomname or room} - no power level set"
                            )
                        else:
                            # Bot is not creator in target room - set appropriate power level
                            filtered_user_power_levels[self.client.mxid] = 1000
                            self.log.info(
                                f"Bot is not creator in modern target room {roomname or room} - power level set to 1000"
                            )

                        # Merge filtered power levels with existing room power levels
                        room_power_levels.users.update(filtered_user_power_levels)

                    elif self.is_modern_room_version(parent_version):
                        # Target room is legacy but parent is modern
                        # Map parent room "creators" to "admins" in legacy room
                        self.log.info(
                            f"Target room {roomname or room} is legacy, parent is modern - mapping creators to admins"
                        )

                        # For legacy rooms, we can set all power levels including the bot
                        # But map parent room creators to appropriate admin levels
                        mapped_power_levels = {}
                        for user, level in user_power_levels.items():
                            if user in parent_creators and user != self.client.mxid:
                                # Map parent creators to admin level (100) in legacy rooms
                                mapped_power_levels[user] = 100
                                self.log.info(
                                    f"Mapping parent creator {user} to admin level 100 in legacy room {roomname or room}"
                                )
                            else:
                                mapped_power_levels[user] = level

                        # Handle bot power level based on whether it's a creator in the parent
                        if self.client.mxid in parent_creators:
                            # Bot is a creator in parent, but this is a legacy room
                            # Set bot to highest power level since creators don't have unlimited power in legacy rooms
                            mapped_power_levels[self.client.mxid] = 1000
                            self.log.info(
                                f"Bot is creator in parent but target is legacy room - setting power level to 1000"
                            )
                        else:
                            # Bot is not a creator in parent, set to highest power level
                            mapped_power_levels[self.client.mxid] = 1000
                            self.log.info(
                                f"Bot is not creator in parent, setting power level to 1000 in legacy target room"
                            )

                        room_power_levels.users = mapped_power_levels

                    else:
                        # Both rooms are legacy - direct power level transfer
                        self.log.info(
                            f"Both rooms are legacy - direct power level transfer"
                        )
                        room_power_levels.users = user_power_levels

                    # Send the updated power levels to this room
                    await self.client.send_state_event(
                        room, EventType.ROOM_POWER_LEVELS, room_power_levels
                    )
                    success_list.append(roomname or room)
                    await asyncio.sleep(self.config["sleep"])

                except Exception as e:
                    self.log.error(
                        f"Failed to update power levels in {roomname or room}: {e}"
                    )
                    error_list.append(roomname or room)

            results = "Power levels synced from parent room.<br /><br />"
            results += f"<b>Parent room version:</b> {parent_version}<br />"
            results += f"<b>Parent room creators:</b> {', '.join(parent_creators) if parent_creators else 'None'}<br />"
            results += f"<b>Bot creator status:</b> {'✅ Creator' if self.client.mxid in parent_creators else '❌ Not creator'} in parent room<br /><br />"

            # Add explanation of power level mapping strategy
            if self.is_modern_room_version(parent_version):
                results += f"<b>Mapping Strategy:</b> Parent room is modern (v{parent_version}), creators have unlimited power.<br />"
                if self.client.mxid in parent_creators:
                    results += "• Bot is creator in parent room (unlimited power)<br />"
                else:
                    results += (
                        "• Bot is not creator in parent room (power level 1000)<br />"
                    )
                results += "• Parent creators mapped to admin level (100) in legacy child rooms<br />"
                results += "• Modern child rooms preserve their creator power levels<br /><br />"
            else:
                results += f"<b>Mapping Strategy:</b> Parent room is legacy (v{parent_version}), using traditional power level system.<br />"
                results += (
                    "• Bot power level set to 1000 for administrative control<br />"
                )
                results += "• Direct power level transfer to legacy child rooms<br />"
                results += "• Modern child rooms preserve their creator power levels<br /><br />"

            if success_list:
                results += f"Successfully updated rooms:<br /><code>{', '.join(success_list)}</code><br /><br />"
            if skipped_list:
                results += f"Skipped rooms due to verification settings:<br /><code>{', '.join(skipped_list)}</code><br /><br />"
            if error_list:
                results += (
                    f"Failed to update rooms:<br /><code>{', '.join(error_list)}</code>"
                )

            await evt.respond(results, allow_html=True, edits=msg)

        except Exception as e:
            error_msg = f"Failed to get parent room power levels: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)

    @room.subcommand(
        "enable-verification",
        help="migrate a room to a verification-based permission model, ensuring current members can still send messages while new joiners require verification",
    )
    @decorators.require_parent_room
    @decorators.require_permission()
    async def room_enable_verification(self, evt: MessageEvent) -> None:
        """Enable verification-based permissions for the current room"""
        await evt.mark_read()

        msg = await evt.respond("Starting room migration...")

        try:
            # Get current room members
            members = await self.client.get_joined_members(evt.room_id)
            member_list = list(members.keys())

            # Get current power levels
            power_levels = await self.client.get_state_event(
                evt.room_id, EventType.ROOM_POWER_LEVELS
            )

            # Get the required power level for sending messages
            events_default = power_levels.events_default
            events = power_levels.events
            required_level = events.get(str(EventType.ROOM_MESSAGE), events_default)

            # Set default power level to n-1 (usually 0)
            power_levels.users_default = required_level - 1

            # Set members to required level only if their current level is lower
            # and they don't have unlimited power (creators in modern room versions)
            for member in member_list:
                # Check if member has unlimited power
                if await self.user_has_unlimited_power(member, evt.room_id):
                    continue  # Skip creators with unlimited power

                current_level = power_levels.get_user_level(member)
                if current_level < required_level:
                    power_levels.users[member] = required_level

            # Send updated power levels
            await self.client.send_state_event(
                evt.room_id, EventType.ROOM_POWER_LEVELS, power_levels
            )

            await evt.respond(
                f"Room migration complete. Current members can send messages, new joiners will require verification.",
                edits=msg,
            )

        except Exception as e:
            error_msg = f"Failed to migrate room: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)

    async def store_verification_state(self, dm_room_id: str, state: dict) -> None:
        """Store verification state in the database."""
        # Try to insert first, if it fails due to existing record, then update
        try:
            insert_query = """INSERT INTO verification_states
                              (dm_room_id, user_id, target_room_id, verification_phrase, attempts_remaining, \
                               required_power_level)
                              VALUES ($1, $2, $3, $4, $5, $6)"""
            await self.database.execute(
                insert_query,
                dm_room_id,
                state["user"],
                state["target_room"],
                state["phrase"],
                state["attempts"],
                state["required_level"],
            )
            self.log.debug(f"Inserted new verification state for {dm_room_id}")
        except Exception as e:
            # If insert fails (likely due to existing record), try update
            if (
                "UNIQUE constraint failed" in str(e)
                or "duplicate key" in str(e).lower()
            ):
                self.log.debug(f"Record exists for {dm_room_id}, updating instead")
                update_query = """UPDATE verification_states
                                  SET verification_phrase  = $4, \
                                      attempts_remaining   = $5, \
                                      required_power_level = $6, \
                                      user_id              = $2, \
                                      target_room_id       = $3 \
                                  WHERE dm_room_id = $1"""
                await self.database.execute(
                    update_query,
                    dm_room_id,
                    state["user"],
                    state["target_room"],
                    state["phrase"],
                    state["attempts"],
                    state["required_level"],
                )
                self.log.debug(f"Updated verification state for {dm_room_id}")
            else:
                # Re-raise if it's not a constraint violation
                raise

    async def get_verification_state(self, dm_room_id: str) -> Optional[dict]:
        """Retrieve verification state from the database."""
        row = await self.database.fetchrow(
            "SELECT * FROM verification_states WHERE dm_room_id = $1", dm_room_id
        )
        if not row:
            return None
        return {
            "user": row["user_id"],
            "target_room": row["target_room_id"],
            "phrase": row["verification_phrase"],
            "attempts": row["attempts_remaining"],
            "required_level": row["required_power_level"],
        }

    async def delete_verification_state(self, dm_room_id: str) -> None:
        """Delete verification state from the database."""
        await self.database.execute(
            "DELETE FROM verification_states WHERE dm_room_id = $1", dm_room_id
        )

    async def cleanup_stale_verification_states(self) -> None:
        """Clean up verification states that are no longer valid."""
        # Get all verification states
        states = await self.database.fetch("SELECT * FROM verification_states")

        for state in states:
            try:
                # Check if DM room still exists and bot is still in it
                try:
                    await self.client.get_state_event(
                        state["dm_room_id"], EventType.ROOM_MEMBER, self.client.mxid
                    )
                except Exception:
                    # Bot is not in the DM room anymore, state is stale
                    await self.delete_verification_state(state["dm_room_id"])
                    continue

                # Check if user is still in the target room
                try:
                    await self.client.get_state_event(
                        state["target_room_id"], EventType.ROOM_MEMBER, state["user_id"]
                    )
                except Exception:
                    # User is not in the target room anymore, state is stale
                    await self.delete_verification_state(state["dm_room_id"])
                    continue

                # Check if verification is too old (older than 24 hours)
                if (datetime.now() - state["created_at"]).total_seconds() > 86400:
                    await self.delete_verification_state(state["dm_room_id"])
                    continue

            except Exception as e:
                self.log.error(
                    f"Error checking verification state {state['dm_room_id']}: {e}"
                )
                # If we can't check the state, assume it's stale
                await self.delete_verification_state(state["dm_room_id"])

    @classmethod
    def get_db_upgrade_table(cls) -> None:
        return upgrade_table

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    @community.subcommand(
        "initialize",
        help="initialize a new community space with the given name. this command can only be used if no parent room is configured.",
    )
    @command.argument("community_name", pass_raw=True, required=True)
    async def initialize_community(
        self, evt: MessageEvent, community_name: str
    ) -> None:
        await evt.mark_read()

        # Check if parent room is already configured
        if self.config["parent_room"]:
            await evt.reply(
                "Cannot initialize: a parent room is already configured. Please remove the parent_room configuration first."
            )
            return

        # Validate community name
        if not community_name or community_name.isspace():
            await evt.reply(
                "Please provide a community name. Usage: !community initialize <community_name>"
            )
            return

        msg = await evt.respond("Initializing new community space...")

        try:
            # Generate community slug if not already set
            if self.config["use_community_slug"] and not self.config["community_slug"]:
                community_slug = self.generate_community_slug(community_name)
                self.config["community_slug"] = community_slug
                self.log.info(f"Generated community slug: {community_slug}")

            # Define child rooms that will be created during initialization (excluding the space itself)
            child_rooms_to_create = [
                f"{community_name} Moderators",  # Moderators room
                f"{community_name} Waiting Room",  # Waiting room
            ]

            # Validate child room aliases before creating any rooms
            is_valid, conflicting_aliases = await self.validate_room_aliases(
                child_rooms_to_create, evt
            )
            if not is_valid:
                error_msg = (
                    f"Cannot initialize community: The following room aliases already exist:\n"
                    + "\n".join(conflicting_aliases)
                )
                await evt.respond(error_msg, edits=msg)
                return

            # Add initiator to invitees list if not already there
            if evt.sender not in self.config["invitees"]:
                self.config["invitees"].append(evt.sender)
            # Save the updated config
            self.config.save()

            # Create the space
            server = self.client.parse_user_id(self.client.mxid)[1]
            sanitized_name = re.sub(r"[^a-zA-Z0-9]", "", community_name).lower()

            # Set up power levels for the space
            power_levels = PowerLevelStateEventContent()

            # Set up power levels for users
            # For modern room versions (12+), the bot (creator) has unlimited power by default
            # but we still need to set power levels for other users
            if self.is_modern_room_version(self.config.get("room_version", "1")):
                # For modern rooms, don't set bot power level (it has unlimited power)
                # but still set power levels for other users
                power_levels.users = {evt.sender: 100}  # Initiator gets admin power
            else:
                # For legacy rooms, set both bot and initiator power levels
                power_levels.users = {
                    self.client.mxid: 1000,  # Bot gets highest power
                    evt.sender: 100,  # Initiator gets admin power
                }

            # Set invite power level from config
            power_levels.invite = self.config.get("invite_power_level", 50)

            # Create the space with appropriate metadata and power levels
            space_id, space_alias = await self.create_space(
                community_name, evt, power_level_override=power_levels
            )

            if not space_id:
                await evt.respond("Failed to create space", edits=msg)
                return

            # Set the space as the parent room in config
            self.config["parent_room"] = space_id
            self.log.info(f"Set parent_room to: {space_id}")

            # Save the updated config
            self.config.save()
            self.log.info("Config saved successfully")

            # Verify the space exists and has correct power levels
            try:
                space_power_levels = await self.client.get_state_event(
                    space_id, EventType.ROOM_POWER_LEVELS
                )

                # For modern room versions, creators have unlimited power and don't appear in power levels
                if self.is_modern_room_version(self.config.get("room_version", "1")):
                    # Just verify the space exists and has power levels
                    if not space_power_levels:
                        raise Exception("Space power levels not set correctly")
                    self.log.info("Space power levels verified for modern room version")
                else:
                    # For legacy room versions, check that bot has admin power
                    if space_power_levels.users.get(self.client.mxid) != 1000:
                        raise Exception("Space power levels not set correctly")
                    self.log.info("Space power levels verified for legacy room version")
            except Exception as e:
                error_msg = f"Failed to verify space setup: {e}"
                self.log.error(error_msg)
                await evt.respond(error_msg, edits=msg)
                return

            # Create moderators room
            # Include the initiator as a moderator, plus any other moderators from the space
            moderators = [evt.sender]  # Always include the initiator

            # Also get any other moderators from the space
            try:
                space_moderators = await self.get_moderators_and_above()
                if space_moderators:
                    # Add other moderators, excluding the bot and the initiator (already added)
                    for user in space_moderators:
                        if user != self.client.mxid and user != evt.sender:
                            moderators.append(user)
            except Exception as e:
                self.log.warning(f"Could not get additional moderators from space: {e}")

            self.log.info(
                f"Moderators room will be created with initial members: {moderators}"
            )

            room_result = await self.create_room(
                f"{community_name} Moderators",
                evt,
                invitees=moderators,  # Use moderators list instead of config invitees
            )

            if not room_result:
                error_msg = "Failed to create moderators room"
                self.log.error(error_msg)
                await evt.respond(error_msg, edits=msg)
                return

            mod_room_id, mod_room_alias = room_result

            # Set moderators room to invite-only
            await self.client.send_state_event(
                mod_room_id,
                EventType.ROOM_JOIN_RULES,
                JoinRulesStateEventContent(join_rule=JoinRule.INVITE),
            )

            # Create waiting room (force unencrypted for public access)
            waiting_room_result = await self.create_room(
                f"{community_name} Waiting Room --unencrypted",
                evt,
                creation_content={
                    "m.federate": True,
                    "m.room.history_visibility": "joined",
                },
            )

            if not waiting_room_result:
                error_msg = "Failed to create waiting room"
                self.log.error(error_msg)
                await evt.respond(error_msg, edits=msg)
                return

            waiting_room_id, waiting_room_alias = waiting_room_result

            # Set waiting room to be joinable by anyone
            await self.client.send_state_event(
                waiting_room_id,
                EventType.ROOM_JOIN_RULES,
                JoinRulesStateEventContent(join_rule=JoinRule.PUBLIC),
            )

            # Update censor configuration based on current value
            current_censor = self.config["censor"]
            if current_censor is False:
                # If censor is false, set it to a list with just the waiting room
                self.config["censor"] = [waiting_room_id]
            elif (
                isinstance(current_censor, list)
                and waiting_room_id not in current_censor
            ):
                # If censor is already a list and waiting room isn't in it, append it
                current_censor.append(waiting_room_id)
                self.config["censor"] = current_censor
            # If censor is True or waiting room is already in the list, leave it as is

            # Save the updated config
            self.config.save()

            # Check if default encryption is enabled and add warning for waiting room
            warning_msg = ""
            if self.config.get("encrypt", False):
                warning_msg = "<br /><br />⚠️ **Note: Waiting room created without encryption (as it is a public room)**"

            await evt.respond(
                f"Community space initialized successfully!<br /><br />"
                f"Community Slug: {self.config['community_slug']}<br />"
                f"Use Community Slug: {self.config['use_community_slug']}"
                f"Room Version: {self.config['room_version']}<br />"
                f"Space: <a href='https://matrix.to/#/{space_alias}'>{space_alias}</a><br />"
                f"Moderators Room: <a href='https://matrix.to/#/{mod_room_alias}'>{mod_room_alias}</a><br />"
                f"Waiting Room: <a href='https://matrix.to/#/{waiting_room_alias}'>{waiting_room_alias}</a>{warning_msg}",
                edits=msg,
                allow_html=True,
            )

        except Exception as e:
            error_msg = f"Failed to initialize community: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)

    @community.subcommand(
        "doctor",
        help="review bot permissions across the space and all rooms to identify potential issues",
    )
    @command.argument("room", required=False)
    async def doctor_check(self, evt: MessageEvent, room: str = None) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        # If a room is specified, show detailed report for that room
        if room:
            await self._doctor_room_detail(evt, room)
            return

        msg = await evt.respond("Running diagnostic check...")

        try:
            report = {"space": {}, "rooms": {}, "issues": [], "warnings": []}

            # Check parent space permissions
            report["space"] = await diagnostic_utils.check_space_permissions(
                self.client, self.config["parent_room"], self.log
            )
            if "error" in report["space"]:
                report["issues"].append(
                    f"Failed to check parent space permissions: {report['space']['error']}"
                )
            elif report["space"].get("bot_power_level", 0) < 100:
                report["issues"].append(
                    f"Bot lacks administrative privileges in parent space (level: {report['space']['bot_power_level']})"
                )

            # Check all rooms in the space
            space_rooms = await self.get_space_roomlist()
            for room_id in space_rooms:
                room_data = await diagnostic_utils.check_room_permissions(
                    self.client, room_id, self.log
                )
                report["rooms"][room_id] = room_data

                # Add issues for problematic rooms
                if "error" in room_data:
                    if room_data["error"] == "Bot not in room":
                        report["issues"].append(
                            f"Bot is not a member of room '{room_id}' that is part of the space"
                        )
                    else:
                        report["issues"].append(
                            f"Failed to check room {room_id}: {room_data['error']}"
                        )
                elif not room_data.get("has_admin", False):
                    report["issues"].append(
                        f"Bot lacks administrative privileges in room '{room_data.get('room_name', room_id)}' ({room_id}) - level: {room_data.get('bot_power_level', 0)}"
                    )

            # Generate response using helper functions
            response = "<h3>🔍 Bot Permission Diagnostic Summary</h3><br /><br />"

            # Space summary - only show if there are issues
            space_has_issues = (
                "error" in report["space"]
                or report["space"].get("bot_power_level", 0) < 100
                or report["space"].get("users_higher")
                or report["space"].get("users_equal")
            )

            if space_has_issues:
                response += diagnostic_utils.generate_space_summary(report["space"])

            # Rooms summary
            room_summary, room_stats = diagnostic_utils.generate_room_summary(
                report["rooms"], self.is_modern_room_version
            )
            response += room_summary

            # Summary statistics
            response += diagnostic_utils.generate_summary_stats(
                report["space"], room_stats
            )

            # Issues and warnings
            response += diagnostic_utils.generate_issues_and_warnings(
                report["issues"], report["warnings"]
            )

            # All clear message if no issues
            if (
                not report["issues"]
                and not report["warnings"]
                and not space_has_issues
                and not room_summary
            ):
                response += diagnostic_utils.generate_all_clear_message()

            # Try to send the response, and if it's too large, break it up
            try:
                await evt.respond(response, edits=msg, allow_html=True)
            except Exception as e:
                error_str = str(e).lower()
                if any(
                    phrase in error_str
                    for phrase in [
                        "event too large",
                        "413",
                        "payload too large",
                        "message too long",
                    ]
                ):
                    self.log.info(
                        f"Doctor report too large ({len(response)} chars), breaking into multiple messages"
                    )

                    # Break up the response into smaller chunks
                    chunks = self._split_doctor_report(response)
                    self.log.info(f"Split report into {len(chunks)} chunks")

                    # Send the first chunk as an edit to the original message
                    if chunks:
                        await evt.respond(chunks[0], edits=msg, allow_html=True)

                        # Send remaining chunks as new messages
                        for i, chunk in enumerate(chunks[1:], 2):
                            await evt.respond(
                                f"<h4>🔍 Bot Permission Diagnostic Report (Part {i}/{len(chunks)})</h4>\n{chunk}",
                                allow_html=True,
                            )
                            await asyncio.sleep(0.5)  # Small delay between messages
                else:
                    # Re-raise if it's not a size issue
                    raise

        except Exception as e:
            error_msg = f"Failed to run diagnostic check: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)

    def _split_doctor_report(
        self, report_text: str, max_chunk_size: int = 4000
    ) -> list[str]:
        """Split a large doctor report into smaller chunks.

        Args:
            report_text: The full report text to split
            max_chunk_size: Maximum size of each chunk in characters

        Returns:
            list: List of text chunks
        """
        return report_utils.split_doctor_report(report_text, max_chunk_size)

    def _split_by_sections(self, text: str, max_size: int) -> list[str]:
        """Split text by section headers to maintain logical grouping.

        Args:
            text: Text to split
            max_size: Maximum size per chunk

        Returns:
            list: List of text chunks
        """
        return report_utils._split_by_sections(text, max_size)

    async def _doctor_room_detail(self, evt: MessageEvent, room: str) -> None:
        """Generate detailed diagnostic report for a specific room.

        Args:
            evt: The message event
            room: Room ID or alias to analyze
        """
        msg = await evt.respond(f"Analyzing room {room}...")

        try:
            # Resolve room ID if alias provided
            room_id = None
            if room.startswith("#"):
                try:
                    room_info = await self.client.resolve_room_alias(room)
                    room_id = room_info["room_id"]
                except Exception as e:
                    await evt.respond(
                        f"Could not resolve room alias {room}: {e}", edits=msg
                    )
                    return
            elif room.startswith("!"):
                room_id = room
            else:
                await evt.respond(
                    f"Invalid room format. Use room ID (!roomid:server) or alias (#alias:server)",
                    edits=msg,
                )
                return

            # Check if room is in the space
            space_rooms = await self.get_space_roomlist()
            if room_id not in space_rooms:
                await evt.respond(
                    f"Room {room} is not part of the configured space.", edits=msg
                )
                return

            # Get room name
            room_name = room_id
            try:
                room_name_event = await self.client.get_state_event(
                    room_id, EventType.ROOM_NAME
                )
                room_name = room_name_event.name
            except:
                pass

            response = f"<h3>🔍 Detailed Analysis: {room_name}</h3><br />"
            response += f"<b>Room ID:</b> {room_id}<br />"

            # Get room version and creators
            room_version, creators = await self.get_room_version_and_creators(room_id)
            response += f"<b>Room Version:</b> {room_version}<br />"
            if creators:
                response += f"<b>Creators:</b> {', '.join(creators)}<br />"
            response += "<br />"

            # Check if bot is in the room
            try:
                await self.client.get_state_event(
                    room_id, EventType.ROOM_MEMBER, self.client.mxid
                )
                response += (
                    "✅ <b>Bot membership:</b> Bot is a member of this room<br /><br />"
                )
            except Exception:
                response += "❌ <b>Bot membership:</b> Bot is not a member of this room<br /><br />"
                await evt.respond(response, edits=msg, allow_html=True)
                return

            # Get power levels
            try:
                power_levels = await self.client.get_state_event(
                    room_id, EventType.ROOM_POWER_LEVELS
                )
                bot_level = power_levels.get_user_level(self.client.mxid)

                # Check if bot has unlimited power (creator in modern room versions)
                bot_has_unlimited_power = await self.user_has_unlimited_power(
                    self.client.mxid, room_id
                )

                response += f"<h4>📊 Power Level Analysis</h4><br />"
                response += f"• <b>Bot power level:</b> {bot_level}<br />"
                if bot_has_unlimited_power:
                    response += f"• <b>Administrative privileges:</b> ✅ Unlimited Power (Creator)<br />"
                else:
                    response += f"• <b>Administrative privileges:</b> {'✅ Yes' if bot_level >= 100 else '❌ No'}<br />"
                response += (
                    f"• <b>Default user level:</b> {power_levels.users_default}<br />"
                )
                response += f"• <b>Invite level:</b> {power_levels.invite}<br />"
                response += f"• <b>Kick level:</b> {power_levels.kick}<br />"
                response += f"• <b>Ban level:</b> {power_levels.ban}<br />"
                response += f"• <b>Redact level:</b> {power_levels.redact}<br /><br />"

                # Check for users with equal or higher power level
                users_higher = []
                users_equal = []

                for user, level in power_levels.users.items():
                    if user != self.client.mxid and level >= bot_level:
                        if level == bot_level:
                            users_equal.append({"user": user, "level": level})
                        else:
                            users_higher.append({"user": user, "level": level})

                if bot_has_unlimited_power:
                    response += f"<h4>ℹ️ Creator Status</h4><br />"
                    response += f"✅ <b>No power level conflicts relevant:</b> Bot has unlimited power as creator in room version {room_version}<br /><br />"
                else:
                    if users_higher:
                        response += f"<h4>⚠️ Users with Higher Power Level</h4><br />"
                        for user_info in users_higher:
                            response += f"• <b>{user_info['user']}</b> (level: {user_info['level']})<br />"
                        response += "<br />"

                    if users_equal:
                        response += f"<h4>⚠️ Users with Equal Power Level</h4><br />"
                        for user_info in users_equal:
                            response += f"• <b>{user_info['user']}</b> (level: {user_info['level']})<br />"
                        response += "<br />"

                    if not users_higher and not users_equal:
                        response += (
                            "✅ <b>No power level conflicts detected</b><br /><br />"
                        )

                # Add note about creators in modern room versions
                if self.is_modern_room_version(room_version):
                    response += f"<h4>ℹ️ Modern Room Version Note</h4><br />"
                    response += f"This room uses version {room_version}, which means creators have unlimited power and cannot be restricted by power levels.<br /><br />"

                # Check specific permissions
                response += f"<h4>🔐 Permission Analysis</h4><br />"

                # Get required levels for various actions
                events_default = power_levels.events_default
                events = power_levels.events

                permissions = [
                    (
                        "Send messages",
                        events.get(str(EventType.ROOM_MESSAGE), events_default),
                    ),
                    ("Send state events", power_levels.state_default),
                    (
                        "Change power levels",
                        events.get(str(EventType.ROOM_POWER_LEVELS), events_default),
                    ),
                    ("Send tombstone", events.get("m.room.tombstone", events_default)),
                    ("Invite users", power_levels.invite),
                    ("Kick users", power_levels.kick),
                    ("Ban users", power_levels.ban),
                    ("Redact messages", power_levels.redact),
                ]

                for perm_name, required_level in permissions:
                    has_perm = bot_level >= required_level or bot_has_unlimited_power
                    status = "✅" if has_perm else "❌"
                    response += f"• {status} <b>{perm_name}:</b> {'Yes' if has_perm else 'No'} (required: {required_level})<br />"

            except Exception as e:
                response += f"❌ <b>Error getting power levels:</b> {e}<br /><br />"

            # Check room state
            try:
                response += f"<h4>🏠 Room State</h4><br />"

                # Check join rules
                try:
                    join_rules = await self.client.get_state_event(
                        room_id, EventType.ROOM_JOIN_RULES
                    )
                    response += f"• <b>Join rule:</b> {join_rules.join_rule}<br />"
                except:
                    response += "• <b>Join rule:</b> Could not determine<br />"

                # Check encryption
                try:
                    encryption = await self.client.get_state_event(
                        room_id, EventType.ROOM_ENCRYPTION
                    )
                    response += f"• <b>Encryption:</b> ✅ Enabled ({encryption.algorithm})<br />"
                except:
                    response += "• <b>Encryption:</b> ❌ Not enabled<br />"

                # Check space parent
                try:
                    space_parent = await self.client.get_state_event(
                        room_id, EventType.SPACE_PARENT
                    )
                    response += (
                        f"• <b>Space parent:</b> ✅ {space_parent.state_key}<br />"
                    )
                except:
                    response += "• <b>Space parent:</b> ❌ Not set<br />"

            except Exception as e:
                response += f"❌ <b>Error checking room state:</b> {e}<br />"

            await evt.respond(response, edits=msg, allow_html=True)

        except Exception as e:
            error_msg = f"Failed to analyze room {room}: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)
