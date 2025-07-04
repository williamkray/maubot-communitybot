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
    RoomCreatePreset
)
from mautrix.errors import MNotFound
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event

BAN_STATE_EVENT = EventType.find("m.policy.rule.user", EventType.Class.STATE)

# database table related things
from .db import upgrade_table


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("sleep")
        helper.copy("welcome_sleep")
        helper.copy("admins")
        helper.copy("moderators")
        helper.copy("parent_room")
        helper.copy("community_slug")
        helper.copy("track_users")
        helper.copy("track_messages")
        helper.copy("track_reactions")
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


class CommunityBot(Plugin):

    _redaction_tasks: asyncio.Task = None
    _verification_states: Dict[str, Dict] = {}

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.client.add_dispatcher(MembershipEventDispatcher)
        # Start background redaction task
        self._redaction_tasks = asyncio.create_task(self._redaction_loop())
        # Clean up stale verification states
        await self.cleanup_stale_verification_states()

    async def stop(self) -> None:
        if self._redaction_tasks:
            self._redaction_tasks.cancel()
        await super().stop()

    async def user_permitted(self, user_id: UserID, min_level: int = 50) -> bool:
        """Check if a user has sufficient power level in the parent room.

        Args:
            user_id: The Matrix ID of the user to check
            min_level: Minimum required power level (default 50 for moderator)

        Returns:
            bool: True if user has sufficient power level
        """
        try:
            power_levels = await self.client.get_state_event(
                self.config["parent_room"], EventType.ROOM_POWER_LEVELS
            )
            user_level = power_levels.get_user_level(user_id)
            return user_level >= min_level
        except Exception as e:
            self.log.error(f"Failed to check user power level: {e}")
            return False

    def generate_community_slug(self, community_name: str) -> str:
        """Generate a community slug from the community name.
        
        Args:
            community_name: The full community name
            
        Returns:
            str: A slug made from the first letter of each word, lowercase
        """
        # Split by whitespace and get first letter of each word
        words = community_name.strip().split()
        slug = ''.join(word[0].lower() for word in words if word)
        return slug

    async def validate_room_alias(self, alias_localpart: str, server: str) -> bool:
        """Check if a room alias already exists.
        
        Args:
            alias_localpart: The localpart of the alias (without # and :server)
            server: The server domain
            
        Returns:
            bool: True if alias is available, False if it already exists
        """
        try:
            full_alias = f"#{alias_localpart}:{server}"
            await self.client.resolve_room_alias(full_alias)
            # If we get here, the alias exists
            return False
        except MNotFound:
            # Alias doesn't exist, so it's available
            return True
        except Exception as e:
            # For other errors, assume alias is available to be safe
            self.log.warning(f"Error checking alias {full_alias}: {e}")
            return True

    async def validate_room_aliases(self, room_names: list[str], evt: MessageEvent = None) -> tuple[bool, list[str]]:
        """Validate that all room aliases are available.
        
        Args:
            room_names: List of room names to validate
            evt: Optional MessageEvent for progress updates
            
        Returns:
            tuple: (is_valid, list_of_conflicting_aliases)
        """
        if not self.config["community_slug"]:
            if evt:
                await evt.respond("Error: No community slug configured. Please run initialize command first.")
            return False, []
            
        server = self.client.parse_user_id(self.client.mxid)[1]
        conflicting_aliases = []
        
        for room_name in room_names:
            # Clean the room name and create alias
            sanitized_name = re.sub(r"[^a-zA-Z0-9]", "", room_name).lower()
            alias_localpart = f"{sanitized_name}-{self.config['community_slug']}"
            
            # Check if alias is available
            is_available = await self.validate_room_alias(alias_localpart, server)
            if not is_available:
                conflicting_aliases.append(f"#{alias_localpart}:{server}")
        
        return len(conflicting_aliases) == 0, conflicting_aliases

    async def get_moderators_and_above(self) -> list[str]:
        """Get list of users with moderator or higher permissions from the parent space.
        
        Returns:
            list: List of user IDs with power level >= 50 (moderator or above)
        """
        try:
            power_levels = await self.client.get_state_event(
                self.config["parent_room"], EventType.ROOM_POWER_LEVELS
            )
            moderators = []
            for user, level in power_levels.users.items():
                if level >= 50:  # Moderator level or above
                    moderators.append(user)
            return moderators
        except Exception as e:
            self.log.error(f"Failed to get moderators from parent space: {e}")
            return []

    async def create_space(self, space_name: str, evt: MessageEvent = None, power_level_override: Optional[PowerLevelStateEventContent] = None) -> tuple[str, str]:
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
            invitees = self.config["invitees"]
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
                    f"creating space {sanitized_name}, give me a minute..."
                )

            # Create the space with space-specific content
            space_id = await self.client.create_room(
                alias_localpart=sanitized_name,
                name=space_name,
                invitees=invitees,
                power_level_override=power_level_override,
                creation_content={"type": "m.space"}
            )

            if evt:
                await evt.respond(
                    f"<a href='https://matrix.to/#/#{sanitized_name}:{server}'>#{sanitized_name}:{server}</a> has been created.",
                    edits=mymsg,
                    allow_html=True
                )

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
        if not self.config["track_users"]:
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

    async def get_space_roomlist(self) -> None:
        space = self.config["parent_room"]
        rooms = []
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
        report = {}
        report["warn_inactive"] = [row["mxid"] for row in warn_inactive_results] or [
            "none"
        ]
        report["kick_inactive"] = [row["mxid"] for row in kick_inactive_results] or [
            "none"
        ]
        report["ignored"] = [row["mxid"] for row in ignored_results] or ["none"]

        return report

    def flag_message(self, msg):
        if msg.content.msgtype in [
            MessageType.FILE,
            MessageType.IMAGE,
            MessageType.VIDEO,
        ]:
            return self.config["censor_files"]

        for w in self.config["censor_wordlist"]:
            try:
                if bool(re.search(w, msg.content.body, re.IGNORECASE)):
                    # self.log.debug(f"DEBUG message flagged for censorship")
                    return True
                else:
                    pass
            except Exception as e:
                self.log.error(f"Could not parse message for flagging: {e}")

    def flag_instaban(self, msg):
        for w in self.config["censor_wordlist_instaban"]:
            try:
                if bool(re.search(w, msg.content.body, re.IGNORECASE)):
                    # self.log.debug(f"DEBUG message flagged for instaban")
                    return True
                else:
                    pass
            except Exception as e:
                self.log.error(f"Could not parse message for flagging: {e}")

    def censor_room(self, msg):
        if isinstance(self.config["censor"], bool):
            # self.log.debug(f"DEBUG message will be redacted because censoring is enabled")
            return self.config["censor"]
        elif isinstance(self.config["censor"], list):
            if msg.room_id in self.config["censor"]:
                # self.log.debug(f"DEBUG message will be redacted because censoring is enabled for THIS room")
                return True
        else:
            return False

    async def check_if_banned(self, userid):
        # fetch banlist data
        is_banned = False
        myrooms = await self.client.get_joined_rooms()
        banlist_roomids = await self.get_banlist_roomids()

        for list_id in banlist_roomids:
            if list_id not in myrooms:
                self.log.error(
                    f"Bot must be in {list_id} before attempting to use it as a banlist."
                )
                pass

            # self.log.debug(f"DEBUG looking up state in {list_id}")
            list_state = await self.client.get_state(list_id)
            # self.log.debug(f"DEBUG state found: {list_state}")
            try:
                user_policies = list(
                    filter(lambda p: p.type.t == "m.policy.rule.user", list_state)
                )
                # self.log.debug(f"DEBUG user policies found: {user_policies}")
            except Exception as e:
                self.log.error(e)

            for rule in user_policies:
                # self.log.debug(f"Checking match of user {userid} in banlist {l} for {rule['content']}")
                try:
                    if bool(
                        fnmatch.fnmatch(userid, rule["content"]["entity"])
                    ) and bool(re.search("ban$", rule["content"]["recommendation"])):
                        # self.log.debug(f"DEBUG user {userid} matches ban rule {rule['content']['entity']}!")
                        return True
                    else:
                        pass
                except Exception as e:
                    # commenting this out because it generates a lot of noise
                    #self.log.debug(
                    #    f"Found something funny in the banlist {list_id} for {rule['content']}: {e}"
                    #)
                    pass
        # if we haven't exited by now, we must not be banned!
        return is_banned

    async def get_messages_to_redact(self, room_id, mxid):
        try:
            messages = await self.client.get_messages(
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
            self.log.debug(
                f"DEBUG found {len(filtered_events)} messages to redact in {room_id} (after filtering empty content)"
            )
            return filtered_events
        except Exception as e:
            self.log.error(f"Error getting messages to redact: {e}")
            return []

    async def redact_messages(self, room_id):
        counters = {"success": 0, "failure": 0}
        sleep_time = self.config["sleep"]
        events = await self.database.fetch(
            "SELECT event_id FROM redaction_tasks WHERE room_id = $1", room_id
        )
        for event in events:
            try:
                await self.client.redact(
                    room_id, event["event_id"], reason="content removed"
                )
                counters["success"] += 1
                await self.database.execute(
                    "DELETE FROM redaction_tasks WHERE event_id = $1", event["event_id"]
                )
                await asyncio.sleep(sleep_time)
            except Exception as e:
                if "Too Many Requests" in str(e):
                    self.log.warning(
                        f"Rate limited while redacting messages in {room_id}, will try again in next loop"
                    )
                    return counters
                self.log.error(f"Failed to redact message: {e}")
                counters["failure"] += 1
                await asyncio.sleep(sleep_time)
        return counters

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
        ban_event_map = {"ban_list": {}, "error_list": {}}

        ban_event_map["ban_list"][user] = []
        for room in roomlist:
            try:
                roomname = None
                roomnamestate = await self.client.get_state_event(room, "m.room.name")
                roomname = roomnamestate["name"]

                # ban user even if they're not in the room!
                if all_rooms:
                    pass
                else:
                    await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)

                await self.client.ban_user(room, user, reason=reason)
                if roomname:
                    ban_event_map["ban_list"][user].append(roomname)
                else:
                    ban_event_map["ban_list"][user].append(room)
                time.sleep(self.config["sleep"])
            except MNotFound:
                pass
            except Exception as e:
                self.log.warning(e)
                ban_event_map["error_list"][user] = []
                ban_event_map["error_list"][user].append(roomname or room)

            if self.config["redact_on_ban"]:
                messages = await self.get_messages_to_redact(room, user)
                # Queue messages for redaction
                for msg in messages:
                    await self.database.execute(
                        "INSERT INTO redaction_tasks (event_id, room_id) VALUES ($1, $2)",
                        msg.event_id,
                        room,
                    )
                self.log.info(
                    f"Queued {len(messages)} messages for redaction in {roomname or room}"
                )

        return ban_event_map

    async def get_banlist_roomids(self):
        banlist_roomids = []
        for l in self.config["banlists"]:
            # self.log.debug(f"DEBUG getting banlist {l}")
            if l.startswith("#"):
                try:
                    l_id = await self.client.resolve_room_alias(l)
                    list_id = l_id["room_id"]
                    time.sleep(self.config["sleep"])
                    # self.log.debug(f"DEBUG banlist id resolves to: {list_id}")
                    banlist_roomids.append(list_id)
                except Exception as e:
                    self.log.error(f"Banlist fetching failed for {l}: {e}")
                    continue
            else:
                list_id = l
                banlist_roomids.append(list_id)

        return banlist_roomids

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

                try:
                    roomname = (
                        await self.client.get_state_event(room_id, "m.room.name")
                    )["name"]
                except:
                    self.log.warning(f"Unable to get room name for {room_id}")

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
            self.log.debug(f"Sync stream leave event for {evt.state_key} in {evt.room_id} detected")
            return
        else:
            # check if the room the person left is protected by check_if_human
            # kick and ban events are sent by other people, so we need to use the state_key
            # when referring to the user who left
            user_id = evt.state_key
            self.log.debug(f"membership change event for {user_id} in {evt.room_id} detected")
            if (
                isinstance(self.config["check_if_human"], bool) and self.config["check_if_human"]
            ) or (
                isinstance(self.config["check_if_human"], list) and evt.room_id in self.config["check_if_human"]
            ):
                self.log.debug(f"Checking if {user_id} is a verified user in {evt.room_id}")
                pl_state = await self.client.get_state_event(evt.room_id, EventType.ROOM_POWER_LEVELS)
                try:
                    user_level = pl_state.get_user_level(user_id)
                except Exception as e:
                    self.log.error(f"Failed to get user level for {user_id} in {evt.room_id}: {e}")
                    return
                default_level = pl_state.users_default
                self.log.debug(f"User {user_id} has power level {user_level}, default level is {default_level}")
                if user_level == ( default_level + 1 ): # indicates verified user
                    self.log.debug(f"Removing {user_id} from power levels state event in {evt.room_id}")
                    pl_state.users.pop(user_id)
                    try:
                        await self.client.send_state_event(evt.room_id, EventType.ROOM_POWER_LEVELS, pl_state)
                    except Exception as e:
                        self.log.error(f"Failed to update power levels state event in {evt.room_id}: {e}")

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
            self.log.debug(f"Verification phrases config: {self.config['verification_phrases']}")
            
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
                        verification_enabled = evt.room_id in self.config["check_if_human"]
                    
                    self.log.debug(f"Verification enabled for room {room_id}: {verification_enabled}")
                    
                    if not verification_enabled:
                        return

                    # Get room name for greeting
                    roomname = "this room"
                    try:
                        roomnamestate = await self.client.get_state_event(evt.room_id, "m.room.name")
                        roomname = roomnamestate["name"]
                    except:
                        pass

                    # Check if user already has sufficient power level
                    try:
                        power_levels = await self.client.get_state_event(
                            evt.room_id, EventType.ROOM_POWER_LEVELS
                        )
                        user_level = power_levels.get_user_level(evt.sender)
                        events_default = power_levels.events_default
                        events = power_levels.events
                        
                        # Get the required power level for sending messages
                        required_level = events.get(str(EventType.ROOM_MESSAGE), events_default)
                        
                        self.log.debug(f"User {evt.sender} has power level {user_level}, required level is {required_level}")
                        
                        # If user already has sufficient power level, skip verification
                        if user_level >= required_level:
                            self.log.debug(f"User {evt.sender} already has sufficient power level ({user_level} >= {required_level})")
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
                                        "content": {"name": f"[{roomname}] join verification"}
                                    }
                                ]
                            )
                            self.log.info(f"Created DM room {dm_room} for {evt.sender}")
                            break
                        except Exception as e:
                            last_error = e
                            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                                self.log.warning(f"Failed to create DM room (attempt {attempt + 1}/{max_retries}): {e}")
                                await asyncio.sleep(retry_delay)
                            else:
                                self.log.error(f"Failed to initiate verification process after {max_retries} attempts: {e}")
                                return

                    # Select random verification phrase
                    verification_phrase = random.choice(self.config["verification_phrases"])
                    
                    # Store verification state
                    verification_state = {
                        "user": evt.sender,
                        "target_room": evt.room_id,
                        "phrase": verification_phrase,
                        "attempts": self.config["verification_attempts"],
                        "required_level": required_level
                    }
                    await self.store_verification_state(dm_room, verification_state)

                    # Send greeting
                    greeting = self.config["verification_message"].format(
                        room=roomname,
                        phrase=verification_phrase
                    )
                    await self.client.send_notice(dm_room, html=greeting)
                    self.log.info(f"Started verification process for {evt.sender} in room {room_id} for room {roomname}")

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

        #self.log.debug(f"Checking verification for {evt.sender} in {evt.room_id}")
        user_phrase = evt.content.body.strip().lower()
        expected_phrase = state["phrase"].lower()

        # Remove punctuation and compare
        user_phrase = re.sub(r'[^\w\s]', '', user_phrase)
        expected_phrase = re.sub(r'[^\w\s]', '', expected_phrase)

        if user_phrase == expected_phrase:
            try:
                # confirm user is still in target room
                members = await self.client.get_joined_members(state["target_room"])
                if state["user"] not in members:
                    await self.client.send_notice(evt.room_id, "Looks like you've left the target room. Rejoin to try again.")
                else:
                    # Update power levels in target room
                    power_levels = await self.client.get_state_event(
                        state["target_room"], EventType.ROOM_POWER_LEVELS
                    )
                    power_levels.users[state["user"]] = state["required_level"]
                    await self.client.send_state_event(
                        state["target_room"], EventType.ROOM_POWER_LEVELS, power_levels
                    )
                    await self.client.send_notice(evt.room_id, "Success! My work here is done. You can leave this room now.")
            except Exception as e:
                await self.client.send_notice(
                    evt.room_id, 
                    f"Something went wrong: {str(e)}. Please report this to the room moderators."
                )
                if self.config["notification_room"]:
                    await self.client.send_notice(
                        self.config["notification_room"],
                        f"User verification failed for {evt.sender} in room {evt.room_id}, you may need to manually verify them."
                    )
            finally:
                await self.client.leave_room(evt.room_id)
                await self.delete_verification_state(evt.room_id)
        else:
            state["attempts"] -= 1
            if state["attempts"] <= 0:
                await self.client.send_notice(
                    evt.room_id, 
                    "You have run out of attempts. Please contact a room moderator for assistance."
                )
                if self.config["notification_room"]:
                    await self.client.send_notice(
                        self.config["notification_room"],
                        f"User verification failed for {evt.sender} in room {evt.room_id}, you may need to manually verify them."
                    )
                await self.client.leave_room(evt.room_id)
                await self.delete_verification_state(evt.room_id)
            else:
                await self.store_verification_state(evt.room_id, state)
                await self.client.send_notice(
                    evt.room_id, 
                    f"Phrase does not match, you have {state['attempts']} tries remaining."
                )

    async def upsert_user_timestamp(self, mxid: str, timestamp: int) -> None:
        """Database-agnostic upsert for user timestamp updates."""
        try:
            # Try insert first
            await self.database.execute(
                "INSERT INTO user_events(mxid, last_message_timestamp) VALUES ($1, $2)",
                mxid, timestamp
            )
        except Exception as e:
            # If insert fails due to existing record, update instead
            if "UNIQUE constraint failed" in str(e) or "duplicate key" in str(e).lower():
                await self.database.execute(
                    "UPDATE user_events SET last_message_timestamp = $2 WHERE mxid = $1",
                    mxid, timestamp
                )
            else:
                # Re-raise if it's not a constraint violation
                raise

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

        if not self.config["track_messages"] or not self.config["track_users"]:
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
        if not self.config["track_reactions"] or not self.config["track_users"]:
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

    @community.subcommand(
        "bancheck", help="check subscribed banlists for a user's mxid"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    async def check_banlists(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        ban_status = await self.check_if_banned(mxid)
        await evt.reply(f"user on banlist: {ban_status}")

    @community.subcommand(
        "sync",
        help="update the activity tracker with the current space members \
            in case they are missing",
    )
    async def sync_space_members(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        # check config values for admins and moderators. if they have a lower PL in the parent room,
        # attempt to update the parent room with their appropriate admin/mod status
        # we can skip all of this logic if those config values are empty
        # this logic helps migrate explicit configuration to the parent-room inheritance model
        if not self.config["admins"] and not self.config["moderators"]:
            self.log.info(
                "no admins or moderators configured, skipping power level sync"
            )
        else:
            power_levels = await self.client.get_state_event(
                self.config["parent_room"], EventType.ROOM_POWER_LEVELS
            )
            users = power_levels.get("users", {})
            for user in self.config["admins"]:
                if user not in users or users.get(user) < 100:
                    # update the users object in-place
                    users[user] = 100

            for user in self.config["moderators"]:
                if user not in users or users.get(user) < 50:
                    # update the users object in-place
                    users[user] = 50

            try:
                # update full powerlevels object with updated user object
                power_levels["users"] = users
                await self.client.send_state_event(
                    self.config["parent_room"],
                    EventType.ROOM_POWER_LEVELS,
                    power_levels,
                )
                # if updating was successful, let's go ahead and clear out the values in the config
                self.config["admins"] = []
                self.config["moderators"] = []
                # and save the config to the file
                self.config.save()
                self.log.debug("successfully migrated admin/mod config to parent room")
            except Exception as e:
                self.log.error(
                    f"Failed to send power levels to {self.config['parent_room']}: {e}"
                )
                await evt.respond(
                    f"Failed to send power levels to {self.config['parent_room']}: {e}"
                )

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
        "ignore", help="exclude a specific matrix ID from inactivity tracking"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    async def ignore_inactivity(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not self.config["track_users"]:
            await evt.reply("user tracking is disabled")
            return

        try:
            Client.parse_user_id(mxid)
            await self.database.execute(
                "UPDATE user_events SET ignore_inactivity = 1 WHERE \
                    mxid = $1",
                mxid,
            )
            self.log.info(f"{mxid} set to ignore inactivity")
            await evt.react("✅")
        except Exception as e:
            await evt.respond(f"{e}")

    @community.subcommand(
        "unignore", help="re-enable activity tracking for a specific matrix ID"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    async def unignore_inactivity(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not self.config["track_users"]:
            await evt.reply("user tracking is disabled")
            return

        try:
            Client.parse_user_id(mxid)
            await self.database.execute(
                "UPDATE user_events SET ignore_inactivity = 0 WHERE \
                    mxid = $1",
                mxid,
            )
            self.log.info(f"{mxid} set to track inactivity")
            await evt.react("✅")
        except Exception as e:
            await evt.respond(f"{e}")

    @community.subcommand(
        "report", help="generate a full list of activity tracking status"
    )
    async def get_report(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not self.config["track_users"]:
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

    @community.subcommand(
        "inactive", help="generate a list of mxids who have been inactive"
    )
    async def get_inactive_report(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not self.config["track_users"]:
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

    @community.subcommand(
        "purgable", help="generate a list of matrix IDs that have been inactive long enough to be purged"
    )
    async def get_purgable_report(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not self.config["track_users"]:
            await evt.reply("user tracking is disabled")
            return

        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(
            f"<p><b>Users inactive for at least {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['kick_inactive'])} <br /></p>",
            allow_html=True,
        )

    @community.subcommand(
        "ignored", help="generate a list of matrix IDs that have activity tracking disabled"
    )
    async def get_ignored_report(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not self.config["track_users"]:
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
    async def kick_users(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

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

                    await self.client.get_state_event(
                        room, EventType.ROOM_MEMBER, user
                    )
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

    @community.subcommand(
        "kick", help="kick a specific user from the community and all rooms"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    async def kick_user(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        user = mxid
        msg = await evt.respond("starting the purge...")
        roomlist = await self.get_space_roomlist()
        # don't forget to kick from the space itself
        roomlist.append(self.config["parent_room"])
        purge_list = {}
        error_list = {}

        purge_list[user] = []
        for room in roomlist:
            try:
                roomname = None
                roomnamestate = await self.client.get_state_event(
                    room, "m.room.name"
                )
                roomname = roomnamestate["name"]

                await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                await self.client.kick_user(room, user, reason="kicked")
                if roomname:
                    purge_list[user].append(roomname)
                else:
                    purge_list[user].append(room)
                time.sleep(self.config["sleep"])
            except MNotFound:
                pass
            except Exception as e:
                self.log.warning(e)
                error_list[user] = []
                error_list[user].append(roomname or room)

        results = "the following users were kicked:<p><code>{purge_list}</code></p>the following errors were \
                recorded:<p><code>{error_list}</code></p>".format(
            purge_list=purge_list, error_list=error_list
        )
        await evt.respond(results, allow_html=True, edits=msg)

        # sync our database after we've made changes to room memberships
        await self.do_sync()

    @community.subcommand(
        "ban", help="kick and ban a specific user from the community and all rooms"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    async def ban_user(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

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

    @community.subcommand(
        "unban", help="unban a specific user from the community and all rooms"
    )
    @command.argument("mxid", "full matrix ID", required=True)
    async def unban_user(self, evt: MessageEvent, mxid: UserID) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

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
                roomnamestate = await self.client.get_state_event(
                    room, "m.room.name"
                )
                roomname = roomnamestate["name"]

                await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                await self.client.unban_user(room, user, reason="unbanned")
                if roomname:
                    unban_list[user].append(roomname)
                else:
                    unban_list[user].append(room)
                time.sleep(self.config["sleep"])
            except MNotFound:
                pass
            except Exception as e:
                self.log.warning(e)
                error_list[user] = []
                error_list[user].append(roomname or room)

        results = "the following users were unbanned:<p><code>{unban_list}</code></p>the following errors were \
                recorded:<p><code>{error_list}</code></p>".format(
            unban_list=unban_list, error_list=error_list
        )
        await evt.respond(results, allow_html=True, edits=msg)

        # sync our database after we've made changes to room memberships
        await self.do_sync()

    @community.subcommand(
        "redact",
        help="redact messages from a specific user (optionally in a specific room)",
    )
    @command.argument("mxid", "full matrix ID", required=True)
    @command.argument("room", "room ID", required=False)
    async def mark_for_redaction(
        self, evt: MessageEvent, mxid: UserID, room: str
    ) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

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

    async def create_room(self, roomname: str, evt: MessageEvent = None, power_level_override: Optional[PowerLevelStateEventContent] = None, creation_content: Optional[dict] = None, invitees: Optional[list[str]] = None) -> None:
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
        encrypted_flag_regex = re.compile(r"(\s+|^)-+encrypt(ed)?\s?")
        unencrypted_flag_regex = re.compile(r"(\s+|^)-+unencrypt(ed)?\s?")
        force_encryption = bool(encrypted_flag_regex.search(roomname))
        force_unencryption = bool(unencrypted_flag_regex.search(roomname))
        try:
            if force_encryption:
                roomname = encrypted_flag_regex.sub("", roomname)
            if force_unencryption:
                roomname = unencrypted_flag_regex.sub("", roomname)
            sanitized_name = re.sub(r"[^a-zA-Z0-9]", "", roomname).lower()
            # Use provided invitees or fall back to config invitees
            room_invitees = invitees if invitees is not None else self.config["invitees"]
            parent_room = self.config["parent_room"]
            server = self.client.parse_user_id(self.client.mxid)[1]

            # Check if community slug is configured
            if not self.config["community_slug"]:
                error_msg = "No community slug configured. Please run initialize command first."
                self.log.error(error_msg)
                if evt:
                    await evt.respond(error_msg)
                return None

            # Create alias with community slug
            alias_localpart = f"{sanitized_name}-{self.config['community_slug']}"
            
            # Validate that the alias is available
            is_available = await self.validate_room_alias(alias_localpart, server)
            if not is_available:
                error_msg = f"Room alias #{alias_localpart}:{server} already exists. Cannot create room."
                self.log.error(error_msg)
                if evt:
                    await evt.respond(error_msg)
                return None

            # Get power levels from parent room if not provided
            if not power_level_override and parent_room:
                power_levels = await self.client.get_state_event(
                    parent_room, EventType.ROOM_POWER_LEVELS
                )
                user_power_levels = power_levels.users
                # ensure bot has highest power
                user_power_levels[self.client.mxid] = 1000
                power_levels.users = user_power_levels
                power_level_override = power_levels
            elif not power_level_override:
                # If no parent room and no override provided, create default power levels
                power_levels = PowerLevelStateEventContent()
                power_levels.users = {
                    self.client.mxid: 1000,  # Bot gets highest power
                }
                # Set invite power level from config
                power_levels.invite = self.config["invite_power_level"]
                power_level_override = power_levels

            if evt:
                mymsg = await evt.respond(
                    f"creating {alias_localpart}, give me a minute..."
                )

            # Prepare initial state events
            initial_state = []
            
            # Only add space parent state if we have a parent room
            if parent_room:
                initial_state.extend([
                    {
                        "type": str(EventType.SPACE_PARENT),
                        "state_key": parent_room,
                        "content": {
                            "via": [server],
                            "canonical": True
                        }
                    },
                    {
                        "type": str(EventType.ROOM_JOIN_RULES),
                        "content": {
                            "join_rule": "restricted",
                            "allow": [{
                                "type": "m.room_membership",
                                "room_id": parent_room
                            }]
                        }
                    }
                ])

            # Add encryption if needed
            if ( self.config["encrypt"] and not force_unencryption ) or force_encryption:
                initial_state.append({
                    "type": str(EventType.ROOM_ENCRYPTION),
                    "content": {
                        "algorithm": "m.megolm.v1.aes-sha2"
                    }
                })

            # Add history visibility if specified in creation_content
            if creation_content and "m.room.history_visibility" in creation_content:
                initial_state.append({
                    "type": str(EventType.ROOM_HISTORY_VISIBILITY),
                    "content": {
                        "history_visibility": creation_content["m.room.history_visibility"]
                    }
                })

            # Create the room with all initial states
            room_id = await self.client.create_room(
                alias_localpart=alias_localpart,
                name=roomname,
                invitees=room_invitees,
                initial_state=initial_state,
                power_level_override=power_level_override,
                creation_content=creation_content
            )

            # The space child relationship needs to be set in the parent room separately
            if parent_room:
                await self.client.send_state_event(
                    parent_room,
                    EventType.SPACE_CHILD,
                    {
                        "via": [server],
                        "suggested": False
                    },
                    state_key=room_id
                )
                await asyncio.sleep(self.config["sleep"])

            if evt:
                await evt.respond(
                    f"<a href='https://matrix.to/#/#{alias_localpart}:{server}'>#{alias_localpart}:{server}</a> has been created and added to the space.",
                    edits=mymsg,
                    allow_html=True
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

    @community.subcommand(
        "createroom",
        help="create a new room titled <roomname> and add it to the parent space. \
                          optionally include `--encrypted` or `--unencrypted` to force regardless of the default settings.",
    )
    @command.argument("roomname", pass_raw=True, required=True)
    async def create_that_room(self, evt: MessageEvent, roomname: str) -> None:
        if not await self.check_parent_room(evt):
            return
        if (roomname == "help") or len(roomname) == 0:
            await evt.reply(
                'pass me a room name (like "cool topic") and i will create it and add it to the space. \
                            use `--encrypted` or `--unencrypted` to ensure encryption is enabled/disabled at creation time even if that isnt my default \
                            setting.'
            )
            return

        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        # Check if community slug is configured
        if not self.config["community_slug"]:
            await evt.reply("No community slug configured. Please run initialize command first.")
            return

        # Validate the room alias before creating
        is_valid, conflicting_aliases = await self.validate_room_aliases([roomname], evt)
        if not is_valid:
            await evt.reply(f"Cannot create room: {conflicting_aliases[0]} already exists.")
            return

        result = await self.create_room(roomname, evt)
        if not result:
            return  # Error already logged and reported to user by create_room

    @community.subcommand("archive", help="archive a room")
    @command.argument("room", required=False)
    async def archive_room(self, evt: MessageEvent, room: str) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()

        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

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

    @community.subcommand("replaceroom", help="replace a room with a new one")
    @command.argument("room", required=False)
    async def replace_room(self, evt: MessageEvent, room: str) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()

        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

        if not room:
            room = evt.room_id
        # first we need to get relevant room state of the room we want to replace
        # this includes the room name, alias, and join rules
        if room.startswith("#"):
            room_id = await self.client.resolve_room_alias(room)
            room_id = room_id["room_id"]
        else:
            room_id = room

        # Check bot permissions in the old room
        has_perms, error_msg, _ = await self.check_bot_permissions(
            room_id, evt, ["state", "tombstone", "power_levels"]
        )
        if not has_perms:
            await evt.respond(f"Cannot replace room: {error_msg}")
            return

        # Get the room name from the state event
        try:
            room_name_event = await self.client.get_state_event(
                room_id, EventType.ROOM_NAME
            )
            room_name = room_name_event.name
        except Exception as e:
            self.log.warning(f"Failed to get room name: {e}")
            # await evt.respond("Could not find room name in state events")
            pass

        # get the room topic from the state event
        try:
            room_topic_event = await self.client.get_state_event(
                room_id, EventType.ROOM_TOPIC
            )
            room_topic = room_topic_event.topic
        except Exception as e:
            self.log.warning(f"Failed to get room topic: {e}")
            pass

        # Get list of aliases to transfer while removing them from the old room
        aliases_to_transfer = await self.remove_room_aliases(room_id, evt)

        # Check if community slug is configured
        if not self.config["community_slug"]:
            await evt.respond("No community slug configured. Please run initialize command first.")
            return

        # Validate that the new room alias is available
        is_valid, conflicting_aliases = await self.validate_room_aliases([room_name], evt)
        if not is_valid:
            await evt.respond(f"Cannot replace room: {conflicting_aliases[0]} already exists.")
            return

        # Now we can start the process of replacing the room
        # First we need to create the new room. this will create the initial alias,
        # as well as bot defaults such as power levels, initial invitations, encryption,
        # and space membership
        new_room_id, new_room_alias = await self.create_room(room_name, evt)
        if not new_room_id:
            await evt.respond("Failed to create new room")
            return

        # Check bot permissions in the new room
        has_perms, error_msg, _ = await self.check_bot_permissions(
            new_room_id, evt, ["state", "tombstone", "power_levels"]
        )
        if not has_perms:
            await evt.respond(
                f"Created new room but cannot complete replacement: {error_msg}"
            )
            return

        # Transfer the aliases to the new room
        for alias in aliases_to_transfer:
            localpart = alias.split(":")[0][1:]  # Remove # and get localpart
            server = alias.split(":")[1]
            try:
                await self.client.add_room_alias(new_room_id, localpart)
                self.log.info(
                    f"Successfully transferred alias {alias} to new room {new_room_id}"
                )
            except Exception as e:
                # If transfer failed, try to create a modified alias
                modified_alias = f"{localpart}NEW"
                try:
                    await self.client.add_room_alias(new_room_id, modified_alias)
                    self.log.info(
                        f"Successfully transferred modified alias {modified_alias} to new room {new_room_id}"
                    )
                except Exception as e2:
                    self.log.error(
                        f"Failed to transfer modified alias {modified_alias}: {e2}"
                    )

        # Get the room avatar from the old room
        try:
            old_room_avatar = await self.client.get_state_event(
                room_id, EventType.ROOM_AVATAR
            )
            if old_room_avatar and old_room_avatar.url:
                # Set the same avatar in the new room
                await self.client.send_state_event(
                    new_room_id, EventType.ROOM_AVATAR, {"url": old_room_avatar.url}
                )
                self.log.info(
                    f"Successfully copied room avatar to new room {new_room_id}"
                )
        except Exception as e:
            self.log.error(f"Failed to copy room avatar to new room: {e}")
            # await evt.respond(f"Failed to copy room avatar to new room: {e}")

        # Set the room topic in the new room
        try:
            await self.client.send_state_event(
                new_room_id, EventType.ROOM_TOPIC, {"topic": room_topic}
            )
            self.log.info(f"Successfully copied room topic to new room {new_room_id}")
        except Exception as e:
            self.log.error(f"Failed to copy room topic to new room: {e}")
            # await evt.respond(f"Failed to copy room topic to new room: {e}")

        # Archive the old room with a pointer to the new room
        success = await self.do_archive_room(room_id, evt, new_room_id)
        if not success:
            await evt.respond(
                "Failed to archive old room, but new room has been created"
            )

        # update instances of the old room id in any config values that use it
        config_keys = [
            "parent_room",
            "notification_room",
            "censor",
            "check_if_human",
            "banlists",
            "greeting_rooms"
        ]
        
        for key in config_keys:
            value = self.config[key]
            if isinstance(value, str):
                if value == room_id:
                    self.config[key] = new_room_id
            elif isinstance(value, list):
                # Handle lists that might contain room IDs
                if room_id in value:
                    self.config[key] = [new_room_id if x == room_id else x for x in value]
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

    @community.subcommand(
        "guests",
        help="generate a list of members in a room who are not members of the parent space",
    )
    @command.argument("room", required=False)
    async def get_guestlist(self, evt: MessageEvent, room: str) -> None:
        if not await self.check_parent_room(evt):
            return
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

    @community.subcommand(
        "roomid", help="return the matrix room ID of this, or a given, room"
    )
    @command.argument("room", required=False)
    async def get_roomid(self, evt: MessageEvent, room: str) -> None:
        if not await self.check_parent_room(evt):
            return
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

    @community.subcommand(
        "setpower", help="sync user power levels from parent room to all child rooms. this will override existing user power levels in child rooms!"
    )
    @command.argument("target_room", required=False)
    async def set_powerlevels(
        self,
        evt: MessageEvent,
        target_room: str = None
    ) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender, min_level=100):
            await evt.reply("You don't have permission to use this command")
            return

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
            # Get parent room power levels to use as source of truth
            parent_power_levels = await self.client.get_state_event(
                self.config["parent_room"], EventType.ROOM_POWER_LEVELS
            )

            user_power_levels = parent_power_levels.users

            # Ensure bot's power level stays at 1000 for safety
            user_power_levels[self.client.mxid] = 1000

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
                    if (
                        len(roomlist) > 1 and 
                        (
                            (isinstance(self.config["check_if_human"], bool) and self.config["check_if_human"]) or
                            (isinstance(self.config["check_if_human"], list) and room in self.config["check_if_human"])
                        )
                    ):
                        self.log.info(f"Skipping {roomname or room} as it requires human verification. You can explicitly run this command for this room to override.")
                        skipped_list.append(roomname or room)
                        continue

                    # get the room's power levels object
                    room_power_levels = await self.client.get_state_event(
                        room, EventType.ROOM_POWER_LEVELS
                    )

                    # plug our parent power levels into the room's power levels object
                    room_power_levels.users = user_power_levels

                    # Send the parent room's power levels to this room
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

    @community.subcommand(
        "verify-migrate",
        help="migrate a room to a verification-based permission model, ensuring current members can still send messages while new joiners require verification",
    )
    async def verify_migrate(self, evt: MessageEvent) -> None:
        if not await self.check_parent_room(evt):
            return
        await evt.mark_read()
        if not await self.user_permitted(evt.sender):
            await evt.reply("You don't have permission to use this command")
            return

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
            for member in member_list:
                current_level = power_levels.get_user_level(member)
                if current_level < required_level:
                    power_levels.users[member] = required_level

            # Send updated power levels
            await self.client.send_state_event(
                evt.room_id, EventType.ROOM_POWER_LEVELS, power_levels
            )

            await evt.respond(
                f"Room migration complete. Current members can send messages, new joiners will require verification.",
                edits=msg
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
            await self.database.execute(insert_query, dm_room_id,
                                        state["user"],
                                        state["target_room"],
                                        state["phrase"],
                                        state["attempts"],
                                        state["required_level"]
                                        )
            self.log.debug(f"Inserted new verification state for {dm_room_id}")
        except Exception as e:
            # If insert fails (likely due to existing record), try update
            if "UNIQUE constraint failed" in str(e) or "duplicate key" in str(e).lower():
                self.log.debug(f"Record exists for {dm_room_id}, updating instead")
                update_query = """UPDATE verification_states
                                  SET verification_phrase  = $4, \
                                      attempts_remaining   = $5, \
                                      required_power_level = $6, \
                                      user_id              = $2, \
                                      target_room_id       = $3 \
                                  WHERE dm_room_id = $1"""
                await self.database.execute(update_query, dm_room_id,
                                             state["user"],
                                             state["target_room"],
                                             state["phrase"],
                                             state["attempts"],
                                             state["required_level"]
                                             )
                self.log.debug(f"Updated verification state for {dm_room_id}")
            else:
                # Re-raise if it's not a constraint violation
                raise

    async def get_verification_state(self, dm_room_id: str) -> Optional[dict]:
        """Retrieve verification state from the database."""
        row = await self.database.fetchrow(
            "SELECT * FROM verification_states WHERE dm_room_id = $1",
            dm_room_id
        )
        if not row:
            return None
        return {
            "user": row["user_id"],
            "target_room": row["target_room_id"],
            "phrase": row["verification_phrase"],
            "attempts": row["attempts_remaining"],
            "required_level": row["required_power_level"]
        }

    async def delete_verification_state(self, dm_room_id: str) -> None:
        """Delete verification state from the database."""
        await self.database.execute(
            "DELETE FROM verification_states WHERE dm_room_id = $1",
            dm_room_id
        )

    async def cleanup_stale_verification_states(self) -> None:
        """Clean up verification states that are no longer valid."""
        # Get all verification states
        states = await self.database.fetch("SELECT * FROM verification_states")
        
        for state in states:
            try:
                # Check if DM room still exists and bot is still in it
                try:
                    await self.client.get_state_event(state["dm_room_id"], EventType.ROOM_MEMBER, self.client.mxid)
                except Exception:
                    # Bot is not in the DM room anymore, state is stale
                    await self.delete_verification_state(state["dm_room_id"])
                    continue

                # Check if user is still in the target room
                try:
                    await self.client.get_state_event(state["target_room_id"], EventType.ROOM_MEMBER, state["user_id"])
                except Exception:
                    # User is not in the target room anymore, state is stale
                    await self.delete_verification_state(state["dm_room_id"])
                    continue

                # Check if verification is too old (older than 24 hours)
                if (datetime.now() - state["created_at"]).total_seconds() > 86400:
                    await self.delete_verification_state(state["dm_room_id"])
                    continue

            except Exception as e:
                self.log.error(f"Error checking verification state {state['dm_room_id']}: {e}")
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
        help="initialize a new community space with the given name. this command can only be used if no parent room is configured."
    )
    @command.argument("community_name", pass_raw=True, required=True)
    async def initialize_community(self, evt: MessageEvent, community_name: str) -> None:
        await evt.mark_read()

        # Check if parent room is already configured
        if self.config["parent_room"]:
            await evt.reply("Cannot initialize: a parent room is already configured. Please remove the parent_room configuration first.")
            return

        # Validate community name
        if not community_name or community_name.isspace():
            await evt.reply("Please provide a community name. Usage: !community initialize <community_name>")
            return

        msg = await evt.respond("Initializing new community space...")

        try:
            # Generate community slug if not already set
            if not self.config["community_slug"]:
                community_slug = self.generate_community_slug(community_name)
                self.config["community_slug"] = community_slug
                self.log.info(f"Generated community slug: {community_slug}")
            
            # Define child rooms that will be created during initialization (excluding the space itself)
            child_rooms_to_create = [
                f"{community_name} Moderators",  # Moderators room
                f"{community_name} Waiting Room"  # Waiting room
            ]
            
            # Validate child room aliases before creating any rooms
            is_valid, conflicting_aliases = await self.validate_room_aliases(child_rooms_to_create, evt)
            if not is_valid:
                error_msg = f"Cannot initialize community: The following room aliases already exist:\n" + "\n".join(conflicting_aliases)
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
            power_levels.users = {
                self.client.mxid: 1000,  # Bot gets highest power
                evt.sender: 100  # Initiator gets admin power
            }
            # Set invite power level from config
            power_levels.invite = self.config["invite_power_level"]

            # Create the space with appropriate metadata and power levels
            space_id, space_alias = await self.create_space(
                community_name,
                evt,
                power_level_override=power_levels
            )

            if not space_id:
                await evt.respond("Failed to create space", edits=msg)
                return

            # Set the space as the parent room in config
            self.config["parent_room"] = space_id

            # Save the updated config
            self.config.save()

            # Verify the space exists and has correct power levels
            try:
                space_power_levels = await self.client.get_state_event(space_id, EventType.ROOM_POWER_LEVELS)
                if space_power_levels.users.get(self.client.mxid) != 1000:
                    raise Exception("Space power levels not set correctly")
            except Exception as e:
                error_msg = f"Failed to verify space setup: {e}"
                self.log.error(error_msg)
                await evt.respond(error_msg, edits=msg)
                return

            # Create moderators room
            # Get moderators and above from the space instead of using config invitees
            moderators = await self.get_moderators_and_above()
            if not moderators:
                self.log.warning("No moderators found in space, moderators room will be created without initial members")
            else:
                # Filter out the bot's own user ID to prevent self-invitation
                moderators = [user for user in moderators if user != self.client.mxid]
                if not moderators:
                    self.log.info("Only bot found in moderators list, moderators room will be created without initial members")
            
            mod_room_id, mod_room_alias = await self.create_room(
                f"{community_name} Moderators",
                evt,
                invitees=moderators  # Use moderators list instead of config invitees
            )

            # Set moderators room to invite-only
            await self.client.send_state_event(
                mod_room_id,
                EventType.ROOM_JOIN_RULES,
                JoinRulesStateEventContent(join_rule=JoinRule.INVITE)
            )

            # Create waiting room
            waiting_room_id, waiting_room_alias = await self.create_room(
                f"{community_name} Waiting Room --unencrypted",
                evt,
                creation_content={
                    "m.federate": True,
                    "m.room.history_visibility": "joined"
                }
            )

            if not waiting_room_id:
                await evt.respond("Failed to create waiting room", edits=msg)
                return

            # Set waiting room to be joinable by anyone
            await self.client.send_state_event(
                waiting_room_id,
                EventType.ROOM_JOIN_RULES,
                JoinRulesStateEventContent(join_rule=JoinRule.PUBLIC)
            )

            # Update censor configuration based on current value
            current_censor = self.config["censor"]
            if current_censor is False:
                # If censor is false, set it to a list with just the waiting room
                self.config["censor"] = [waiting_room_id]
            elif isinstance(current_censor, list) and waiting_room_id not in current_censor:
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
                f"Space: <a href='https://matrix.to/#/{space_alias}'>{space_alias}</a><br />"
                f"Moderators Room: <a href='https://matrix.to/#/{mod_room_alias}'>{mod_room_alias}</a><br />"
                f"Waiting Room: <a href='https://matrix.to/#/{waiting_room_alias}'>{waiting_room_alias}</a>{warning_msg}",
                edits=msg,
                allow_html=True
            )

        except Exception as e:
            error_msg = f"Failed to initialize community: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)

    @community.subcommand(
        "doctor",
        help="review bot permissions across the space and all rooms to identify potential issues"
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
            report = {
                "space": {},
                "rooms": {},
                "issues": [],
                "warnings": []
            }

            # Check parent space permissions
            try:
                space_power_levels = await self.client.get_state_event(
                    self.config["parent_room"], EventType.ROOM_POWER_LEVELS
                )
                bot_level = space_power_levels.get_user_level(self.client.mxid)
                
                report["space"] = {
                    "room_id": self.config["parent_room"],
                    "bot_power_level": bot_level,
                    "has_admin": bot_level >= 100,
                    "users_higher_or_equal": [],
                    "users_equal": [],
                    "users_higher": []
                }

                # Check for users with equal or higher power level
                for user, level in space_power_levels.users.items():
                    if user != self.client.mxid and level >= bot_level:
                        if level == bot_level:
                            report["space"]["users_equal"].append({
                                "user": user,
                                "level": level
                            })
                        else:
                            report["space"]["users_higher"].append({
                                "user": user,
                                "level": level
                            })
                        report["space"]["users_higher_or_equal"].append({
                            "user": user,
                            "level": level
                        })

                if bot_level < 100:
                    report["issues"].append(f"Bot lacks administrative privileges in parent space (level: {bot_level})")
                
                # Remove verbose warnings from summary - these will be shown in detailed room reports
                # if report["space"]["users_higher"]:
                #     report["warnings"].append(f"Users with higher power level in parent space: {', '.join([f'{u['user']} ({u['level']})' for u in report['space']['users_higher']])}")
                # 
                # if report["space"]["users_equal"]:
                #     report["warnings"].append(f"Users with equal power level in parent space: {', '.join([f'{u['user']} ({u['level']})' for u in report['space']['users_equal']])}")

            except Exception as e:
                report["space"] = {
                    "room_id": self.config["parent_room"],
                    "error": str(e)
                }
                report["issues"].append(f"Failed to check parent space permissions: {e}")

            # Check all rooms in the space
            space_rooms = await self.get_space_roomlist()
            
            for room_id in space_rooms:
                try:
                    # First check if bot is in the room
                    try:
                        await self.client.get_state_event(room_id, EventType.ROOM_MEMBER, self.client.mxid)
                        bot_in_room = True
                    except Exception:
                        bot_in_room = False
                        report["issues"].append(f"Bot is not a member of room '{room_id}' that is part of the space")
                        report["rooms"][room_id] = {
                            "room_id": room_id,
                            "error": "Bot not in room"
                        }
                        continue

                    room_power_levels = await self.client.get_state_event(
                        room_id, EventType.ROOM_POWER_LEVELS
                    )
                    bot_level = room_power_levels.get_user_level(self.client.mxid)
                    
                    # Get room name if available
                    room_name = room_id
                    try:
                        room_name_event = await self.client.get_state_event(room_id, EventType.ROOM_NAME)
                        room_name = room_name_event.name
                    except:
                        pass

                    room_report = {
                        "room_id": room_id,
                        "room_name": room_name,
                        "bot_power_level": bot_level,
                        "has_admin": bot_level >= 100,
                        "users_higher_or_equal": [],
                        "users_equal": [],
                        "users_higher": []
                    }

                    # Check for users with equal or higher power level
                    for user, level in room_power_levels.users.items():
                        if user != self.client.mxid and level >= bot_level:
                            if level == bot_level:
                                room_report["users_equal"].append({
                                    "user": user,
                                    "level": level
                                })
                            else:
                                room_report["users_higher"].append({
                                    "user": user,
                                    "level": level
                                })
                            room_report["users_higher_or_equal"].append({
                                "user": user,
                                "level": level
                            })

                    if bot_level < 100:
                        report["issues"].append(f"Bot lacks administrative privileges in room '{room_name}' ({room_id}) - level: {bot_level}")
                    
                    # Remove verbose warnings from summary - these will be shown in detailed room reports
                    # if room_report["users_higher"]:
                    #     report["warnings"].append(f"Users with higher power level in room '{room_name}': {', '.join([f'{u['user']} ({u['level']})' for u in room_report['users_higher']])}")
                    # 
                    # if room_report["users_equal"]:
                    #     report["warnings"].append(f"Users with equal power level in room '{room_name}': {', '.join([f'{u['user']} ({u['level']})' for u in room_report['users_equal']])}")

                    report["rooms"][room_id] = room_report

                except Exception as e:
                    report["rooms"][room_id] = {
                        "room_id": room_id,
                        "error": str(e)
                    }
                    report["issues"].append(f"Failed to check room {room_id}: {e}")

            # Generate concise summary response
            response = "<h3>🔍 Bot Permission Diagnostic Summary</h3><br /><br />"

            # Space summary - only show if there are issues
            space_has_issues = False
            if "error" in report["space"]:
                space_has_issues = True
                response += "<h4>📋 Parent Space</h4><br />"
                response += f"❌ <b>Error:</b> {report['space']['error']}<br /><br />"
            elif report["space"].get("bot_power_level", 0) < 100 or report["space"].get("users_higher") or report["space"].get("users_equal"):
                space_has_issues = True
                response += "<h4>📋 Parent Space</h4><br />"
                space_status = "✅" if report["space"]["has_admin"] else "❌"
                response += f"{space_status} <b>Administrative privileges:</b> {'Yes' if report['space']['has_admin'] else 'No'} (level: {report['space']['bot_power_level']})<br />"
                if report["space"]["users_higher"]:
                    response += f"⚠️ <b>Users with higher power:</b> {', '.join([f'{u['user']} ({u['level']})' for u in report['space']['users_higher']])}<br />"
                if report["space"]["users_equal"]:
                    response += f"⚠️ <b>Users with equal power:</b> {', '.join([f'{u['user']} ({u['level']})' for u in report['space']['users_equal']])}<br />"
                response += "<br />"

            # Rooms summary - only show problematic rooms with room IDs
            problematic_rooms = []
            admin_rooms = 0
            non_admin_rooms = 0
            error_rooms = 0
            not_in_room_count = 0
            
            for room_id, room_data in report["rooms"].items():
                if "error" in room_data:
                    error_rooms += 1
                    if room_data["error"] == "Bot not in room":
                        not_in_room_count += 1
                        problematic_rooms.append(f"❌ <b>{room_data.get('room_name', room_id)}</b> ({room_id}): Bot not in room")
                    else:
                        problematic_rooms.append(f"❌ <b>{room_data.get('room_name', room_id)}</b> ({room_id}): Error - {room_data['error']}")
                else:
                    if room_data["has_admin"]:
                        admin_rooms += 1
                        # Only show if there are power level conflicts
                        if room_data["users_higher"] or room_data["users_equal"]:
                            room_info = f"⚠️ <b>{room_data['room_name']}</b> ({room_id}): Admin: Yes (level: {room_data['bot_power_level']})"
                            if room_data["users_higher"]:
                                room_info += f" - Higher power users: {len(room_data['users_higher'])}"
                            if room_data["users_equal"]:
                                room_info += f" - Equal power users: {len(room_data['users_equal'])}"
                            problematic_rooms.append(room_info)
                    else:
                        non_admin_rooms += 1
                        problematic_rooms.append(f"❌ <b>{room_data['room_name']}</b> ({room_id}): Admin: No (level: {room_data['bot_power_level']})")

            # Only show rooms section if there are problematic rooms
            if problematic_rooms:
                response += f"<h4>🏠 Problematic Rooms ({len(problematic_rooms)} of {len(report['rooms'])} total)</h4><br />"
                response += "<i>Use <code>!community doctor &lt;room_id&gt;</code> for detailed analysis of specific rooms</i><br /><br />"
                for room_info in problematic_rooms:
                    response += f"{room_info}<br />"
                response += "<br />"

            # Summary - always show
            response += f"<h4>📊 Summary</h4><br />"
            response += f"• Parent space: {'✅ Admin' if report['space'].get('has_admin', False) else '❌ No admin'}<br />"
            response += f"• Rooms with admin: {admin_rooms}<br />"
            response += f"• Rooms without admin: {non_admin_rooms}<br />"
            if not_in_room_count > 0:
                response += f"• Rooms bot not in: {not_in_room_count}<br />"
            if error_rooms > 0:
                response += f"• Rooms with errors: {error_rooms}<br />"
            response += "<br />"

            # Issues and warnings - only show if they exist
            if report["issues"]:
                response += f"<h4>🚨 Critical Issues</h4><br />"
                for issue in report["issues"]:
                    response += f"• {issue}<br />"
                response += "<br />"

            if report["warnings"]:
                response += f"<h4>⚠️ Warnings</h4><br />"
                for warning in report["warnings"]:
                    response += f"• {warning}<br />"
                response += "<br />"

            if not report["issues"] and not report["warnings"] and not space_has_issues and not problematic_rooms:
                response += f"<h4>✅ All Clear</h4><br />"
                response += "No permission issues detected. The bot should be able to manage all rooms and users effectively.<br />"

            # Try to send the response, and if it's too large, break it up
            try:
                await evt.respond(response, edits=msg, allow_html=True)
            except Exception as e:
                error_str = str(e).lower()
                if any(phrase in error_str for phrase in ["event too large", "413", "payload too large", "message too long"]):
                    self.log.info(f"Doctor report too large ({len(response)} chars), breaking into multiple messages")
                    
                    # Break up the response into smaller chunks
                    chunks = self._split_doctor_report(response)
                    self.log.info(f"Split report into {len(chunks)} chunks")
                    
                    # Send the first chunk as an edit to the original message
                    if chunks:
                        await evt.respond(chunks[0], edits=msg, allow_html=True)
                        
                        # Send remaining chunks as new messages
                        for i, chunk in enumerate(chunks[1:], 2):
                            await evt.respond(f"<h4>🔍 Bot Permission Diagnostic Report (Part {i}/{len(chunks)})</h4>\n{chunk}", allow_html=True)
                            await asyncio.sleep(0.5)  # Small delay between messages
                else:
                    # Re-raise if it's not a size issue
                    raise

        except Exception as e:
            error_msg = f"Failed to run diagnostic check: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)

    def _split_doctor_report(self, report_text: str, max_chunk_size: int = 4000) -> list[str]:
        """Split a large doctor report into smaller chunks.
        
        Args:
            report_text: The full report text to split
            max_chunk_size: Maximum size of each chunk in characters
            
        Returns:
            list: List of text chunks
        """
        if len(report_text) <= max_chunk_size:
            return [report_text]
        
        chunks = []
        lines = report_text.split('\n')
        current_chunk = ""
        
        for line in lines:
            # If adding this line would exceed the limit, start a new chunk
            if len(current_chunk) + len(line) + 1 > max_chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            
            current_chunk += line + '\n'
        
        # Add the last chunk if it has content
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        # If we still have chunks that are too large, split them more aggressively
        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= max_chunk_size:
                final_chunks.append(chunk)
            else:
                # Split by sections (headers) if possible
                section_chunks = self._split_by_sections(chunk, max_chunk_size)
                final_chunks.extend(section_chunks)
        
        return final_chunks
    
    def _split_by_sections(self, text: str, max_size: int) -> list[str]:
        """Split text by section headers to maintain logical grouping.
        
        Args:
            text: Text to split
            max_size: Maximum size per chunk
            
        Returns:
            list: List of text chunks
        """
        # Split by headers (h3, h4)
        import re
        sections = re.split(r'(<h[34][^>]*>.*?</h[34]>)', text, flags=re.DOTALL)
        
        chunks = []
        current_chunk = ""
        
        for section in sections:
            if len(current_chunk) + len(section) > max_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            current_chunk += section
        
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        return chunks

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
                    await evt.respond(f"Could not resolve room alias {room}: {e}", edits=msg)
                    return
            elif room.startswith("!"):
                room_id = room
            else:
                await evt.respond(f"Invalid room format. Use room ID (!roomid:server) or alias (#alias:server)", edits=msg)
                return

            # Check if room is in the space
            space_rooms = await self.get_space_roomlist()
            if room_id not in space_rooms:
                await evt.respond(f"Room {room} is not part of the configured space.", edits=msg)
                return

            # Get room name
            room_name = room_id
            try:
                room_name_event = await self.client.get_state_event(room_id, EventType.ROOM_NAME)
                room_name = room_name_event.name
            except:
                pass

            response = f"<h3>🔍 Detailed Analysis: {room_name}</h3><br />"
            response += f"<b>Room ID:</b> {room_id}<br /><br />"

            # Check if bot is in the room
            try:
                await self.client.get_state_event(room_id, EventType.ROOM_MEMBER, self.client.mxid)
                response += "✅ <b>Bot membership:</b> Bot is a member of this room<br /><br />"
            except Exception:
                response += "❌ <b>Bot membership:</b> Bot is not a member of this room<br /><br />"
                await evt.respond(response, edits=msg, allow_html=True)
                return

            # Get power levels
            try:
                power_levels = await self.client.get_state_event(room_id, EventType.ROOM_POWER_LEVELS)
                bot_level = power_levels.get_user_level(self.client.mxid)
                
                response += f"<h4>📊 Power Level Analysis</h4><br />"
                response += f"• <b>Bot power level:</b> {bot_level}<br />"
                response += f"• <b>Administrative privileges:</b> {'✅ Yes' if bot_level >= 100 else '❌ No'}<br />"
                response += f"• <b>Default user level:</b> {power_levels.users_default}<br />"
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
                    response += "✅ <b>No power level conflicts detected</b><br /><br />"

                # Check specific permissions
                response += f"<h4>🔐 Permission Analysis</h4><br />"
                
                # Get required levels for various actions
                events_default = power_levels.events_default
                events = power_levels.events
                
                permissions = [
                    ("Send messages", events.get(str(EventType.ROOM_MESSAGE), events_default)),
                    ("Send state events", power_levels.state_default),
                    ("Change power levels", events.get(str(EventType.ROOM_POWER_LEVELS), events_default)),
                    ("Send tombstone", events.get("m.room.tombstone", events_default)),
                    ("Invite users", power_levels.invite),
                    ("Kick users", power_levels.kick),
                    ("Ban users", power_levels.ban),
                    ("Redact messages", power_levels.redact)
                ]
                
                for perm_name, required_level in permissions:
                    has_perm = bot_level >= required_level
                    status = "✅" if has_perm else "❌"
                    response += f"• {status} <b>{perm_name}:</b> {'Yes' if has_perm else 'No'} (required: {required_level})<br />"

            except Exception as e:
                response += f"❌ <b>Error getting power levels:</b> {e}<br /><br />"

            # Check room state
            try:
                response += f"<h4>🏠 Room State</h4><br />"
                
                # Check join rules
                try:
                    join_rules = await self.client.get_state_event(room_id, EventType.ROOM_JOIN_RULES)
                    response += f"• <b>Join rule:</b> {join_rules.join_rule}<br />"
                except:
                    response += "• <b>Join rule:</b> Could not determine<br />"

                # Check encryption
                try:
                    encryption = await self.client.get_state_event(room_id, EventType.ROOM_ENCRYPTION)
                    response += f"• <b>Encryption:</b> ✅ Enabled ({encryption.algorithm})<br />"
                except:
                    response += "• <b>Encryption:</b> ❌ Not enabled<br />"

                # Check space parent
                try:
                    space_parent = await self.client.get_state_event(room_id, EventType.SPACE_PARENT)
                    response += f"• <b>Space parent:</b> ✅ {space_parent.state_key}<br />"
                except:
                    response += "• <b>Space parent:</b> ❌ Not set<br />"

            except Exception as e:
                response += f"❌ <b>Error checking room state:</b> {e}<br />"

            await evt.respond(response, edits=msg, allow_html=True)

        except Exception as e:
            error_msg = f"Failed to analyze room {room}: {e}"
            self.log.error(error_msg)
            await evt.respond(error_msg, edits=msg)
