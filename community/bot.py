# kickbot - a maubot plugin to track user activity and remove inactive users from rooms/spaces.

from typing import Awaitable, Type, Optional, Tuple
import json
import time
import re

from mautrix.client import Client, InternalEventType, MembershipEventDispatcher
from mautrix.types import (Event, StateEvent, EventID, UserID, FileInfo, EventType,
                            MediaMessageEventContent, ReactionEvent, RedactionEvent)
from mautrix.errors import MNotFound
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event

# database table related things
from .db import upgrade_table



class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("admins")
        helper.copy("parent_room")
        helper.copy("track_messages")
        helper.copy("track_reactions")
        helper.copy("warn_threshold_days")
        helper.copy("kick_threshold_days")
        helper.copy("encrypt")
        helper.copy("invitees")


class CommunityBot(Plugin):

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.client.add_dispatcher(MembershipEventDispatcher)


    async def do_sync(self) -> None:
        space_members_obj = await self.client.get_joined_members(self.config["parent_room"])
        space_members_list = space_members_obj.keys()
        table_users = await self.database.fetch("SELECT mxid FROM user_events")
        table_user_list = [ row["mxid"] for row in table_users ]
        untracked_users = set(space_members_list) - set(table_user_list)
        non_space_members = set(table_user_list) - set(space_members_list)
        results = {}
        results['added'] = []
        results['dropped'] = []
        try:
            for user in untracked_users:
                now = int(time.time() * 1000)
                q = """
                    INSERT INTO user_events (mxid, last_message_timestamp)
                    VALUES ($1, $2)
                    """
                await self.database.execute(q, user, now)
                results['added'].append(user)
                self.log.info(f"{user} inserted into activity tracking table")
            for user in non_space_members:
                await self.database.execute("DELETE FROM user_events WHERE mxid = $1", user)
                self.log.info(f"{user} is not a space member, dropped from activity tracking table")
                results['dropped'].append(user)

        except Exception as e:
            self.log.exception(e)

        return results

    async def get_space_roomlist(self) -> None:
        space = self.config["parent_room"]
        rooms = []
        state = await self.client.get_state(space)
        for evt in state:
            if evt.type == EventType.SPACE_CHILD:
                # only look for rooms that include a via path, otherwise they
                # are not really in the space!
                if evt.content and evt.content.via:
                    rooms.append(evt.state_key)
        return rooms

    async def generate_report(self) -> None:
        now = int(time.time() * 1000)
        warn_days_ago = (now - (1000 * 60 * 60 * 24 * self.config["warn_threshold_days"]))
        kick_days_ago = (now - (1000 * 60 * 60 * 24 * self.config["kick_threshold_days"]))
        warn_q = """
            SELECT mxid FROM user_events WHERE last_message_timestamp <= $1 AND 
            last_message_timestamp >= $2
            AND ignore_inactivity = 0
            """
        kick_q = """
            SELECT mxid FROM user_events WHERE last_message_timestamp <= $1
            AND ignore_inactivity = 0
            """
        warn_inactive_results = await self.database.fetch(warn_q, warn_days_ago, kick_days_ago)
        kick_inactive_results = await self.database.fetch(kick_q, kick_days_ago)
        report = {}
        report["warn_inactive"] = [ row["mxid"] for row in warn_inactive_results ] or ["none"]
        report["kick_inactive"] = [ row["mxid"] for row in kick_inactive_results ] or ["none"]

        return report
        
    @event.on(InternalEventType.JOIN)
    async def passive_sync(self, evt:StateEvent) -> None:
        if evt.room_id == self.config['parent_room']:
            await self.do_sync()

    @event.on(EventType.ROOM_MESSAGE)
    async def update_message_timestamp(self, evt: MessageEvent) -> None:
        if not self.config["track_messages"]:
            pass
        else:
            q = """
                INSERT INTO user_events(mxid, last_message_timestamp) 
                VALUES ($1, $2)
                ON CONFLICT(mxid)
                DO UPDATE SET last_message_timestamp=$2
            """
            await self.database.execute(q, evt.sender, evt.timestamp)

    @event.on(EventType.REACTION)
    async def update_reaction_timestamp(self, evt: MessageEvent) -> None:
        if not self.config["track_reactions"]:
            pass
        else:
            q = """
                INSERT INTO user_events(mxid, last_message_timestamp) 
                VALUES ($1, $2)
                ON CONFLICT(mxid)
                DO UPDATE SET last_message_timestamp=$2
            """
            await self.database.execute(q, evt.sender, evt.timestamp)

    @command.new("community", help="track active/inactive status of members of a space")
    async def community(self) -> None:
        pass


    @community.subcommand("sync", help="update the activity tracker with the current space members \
            in case they are missing")
    async def sync_space_members(self, evt: MessageEvent) -> None:
        if evt.sender in self.config["admins"]:
            results = await self.do_sync()

            added_str = "<br />".join(results['added'])
            dropped_str = "<br />".join(results['dropped'])
            await evt.respond(f"Added: {added_str}<br /><br />Dropped: {dropped_str}", allow_html=True)
        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("ignore", help="exclude a specific matrix ID from inactivity tracking")
    @command.argument("mxid", "full matrix ID", required=True)
    async def ignore_inactivity(self, evt: MessageEvent, mxid: UserID) -> None:
        if evt.sender in self.config["admins"]:
            try:
                Client.parse_user_id(mxid)
                await self.database.execute("UPDATE user_events SET ignore_inactivity = 1 WHERE \
                        mxid = $1", mxid)
                self.log.info(f"{mxid} set to ignore inactivity")
                await evt.react("✅")
            except Exception as e:
                await evt.respond(f"{e}")
        else:
            await evt.reply("lol you don't have permission to set that")

    @community.subcommand("unignore", help="re-enable activity tracking for a specific matrix ID")
    @command.argument("mxid", "full matrix ID", required=True)
    async def unignore_inactivity(self, evt: MessageEvent, mxid: UserID) -> None:
        if evt.sender in self.config["admins"]:
            try:
                Client.parse_user_id(mxid)
                await self.database.execute("UPDATE user_events SET ignore_inactivity = 0 WHERE \
                        mxid = $1", mxid)
                self.log.info(f"{mxid} set to track inactivity")
                await evt.react("✅")
            except Exception as e:
                await evt.respond(f"{e}")
        else:
            await evt.reply("lol you don't have permission to set that")

    @community.subcommand("report", help='generate a list of matrix IDs that have been inactive')
    async def get_report(self, evt: MessageEvent) -> None:
        sync_results = await self.do_sync()
        report = await self.generate_report()
        await evt.respond(f"<b>Users inactive for at least {self.config['warn_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['warn_inactive'])} <br />\
                <b>Users inactive for at least {self.config['kick_threshold_days']} days:</b><br /> \
                {'<br />'.join(report['kick_inactive'])}", \
                allow_html=True)


    @community.subcommand("purge", help='kick users for excessive inactivity')
    async def kick_users(self, evt: MessageEvent) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"]:
            msg = await evt.respond("starting the purge...")
            report = await self.generate_report()
            purgeable = report['kick_inactive']
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
                        roomnamestate = await self.client.get_state_event(room, 'm.room.name')
                        roomname = roomnamestate['name']

                        await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                        await self.client.kick_user(room, user, reason='inactivity')
                        if roomname:
                            purge_list[user].append(roomname)
                        else:
                            purge_list[user].append(room)
                        time.sleep(0.5)
                    except MNotFound:
                        pass
                    except Exception as e:
                        self.log.warning(e)
                        error_list[user] = []
                        error_list[user].append(roomname or room)


            results = "the following users were purged:<p><code>{purge_list}</code></p>the following errors were \
                    recorded:<p><code>{error_list}</code></p>".format(purge_list=purge_list, error_list=error_list)
            await evt.respond(results, allow_html=True, edits=msg)
        
            # sync our database after we've made changes to room memberships
            await self.do_sync()

        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("kick", help='kick a specific user from the community and all rooms')
    @command.argument("mxid", "full matrix ID", required=True)
    async def kick_user(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"]:
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
                    roomnamestate = await self.client.get_state_event(room, 'm.room.name')
                    roomname = roomnamestate['name']

                    await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                    await self.client.kick_user(room, user, reason='kicked')
                    if roomname:
                        purge_list[user].append(roomname)
                    else:
                        purge_list[user].append(room)
                    time.sleep(0.5)
                except MNotFound:
                    pass
                except Exception as e:
                    self.log.warning(e)
                    error_list[user] = []
                    error_list[user].append(roomname or room)


            results = "the following users were kicked:<p><code>{purge_list}</code></p>the following errors were \
                    recorded:<p><code>{error_list}</code></p>".format(purge_list=purge_list, error_list=error_list)
            await evt.respond(results, allow_html=True, edits=msg)
        
            # sync our database after we've made changes to room memberships
            await self.do_sync()

        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("ban", help='kick and ban a specific user from the community and all rooms')
    @command.argument("mxid", "full matrix ID", required=True)
    async def ban_user(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"]:
            user = mxid
            msg = await evt.respond("starting the ban...")
            roomlist = await self.get_space_roomlist()
            # don't forget to kick from the space itself
            roomlist.append(self.config["parent_room"])
            ban_list = {}
            error_list = {}

            ban_list[user] = []
            for room in roomlist:
                try:
                    roomname = None
                    roomnamestate = await self.client.get_state_event(room, 'm.room.name')
                    roomname = roomnamestate['name']

                    # ban user even if they're not in the room!
                    #await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                    await self.client.ban_user(room, user, reason='banned')
                    if roomname:
                        ban_list[user].append(roomname)
                    else:
                        ban_list[user].append(room)
                    time.sleep(0.5)
                except MNotFound:
                    pass
                except Exception as e:
                    self.log.warning(e)
                    error_list[user] = []
                    error_list[user].append(roomname or room)


            results = "the following users were kicked and banned:<p><code>{ban_list}</code></p>the following errors were \
                    recorded:<p><code>{error_list}</code></p>".format(ban_list=ban_list, error_list=error_list)
            await evt.respond(results, allow_html=True, edits=msg)
        
            # sync our database after we've made changes to room memberships
            await self.do_sync()

        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("unban", help='unban a specific user from the community and all rooms')
    @command.argument("mxid", "full matrix ID", required=True)
    async def unban_user(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"]:
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
                    roomnamestate = await self.client.get_state_event(room, 'm.room.name')
                    roomname = roomnamestate['name']

                    await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)
                    await self.client.unban_user(room, user, reason='unbanned')
                    if roomname:
                        unban_list[user].append(roomname)
                    else:
                        unban_list[user].append(room)
                    time.sleep(0.5)
                except MNotFound:
                    pass
                except Exception as e:
                    self.log.warning(e)
                    error_list[user] = []
                    error_list[user].append(roomname or room)


            results = "the following users were unbanned:<p><code>{unban_list}</code></p>the following errors were \
                    recorded:<p><code>{error_list}</code></p>".format(unban_list=unban_list, error_list=error_list)
            await evt.respond(results, allow_html=True, edits=msg)
        
            # sync our database after we've made changes to room memberships
            await self.do_sync()

        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("createroom", help="create a new room titled <roomname> and add it to the parent space")
    @command.argument("roomname", pass_raw=True, required=True)
    async def create_that_room(self, evt: MessageEvent, roomname: str) -> None:
        if (roomname == "help") or len(roomname) == 0:
            self.log.debug(f"DEBUG: {homeserver}")
            await evt.reply('pass me a room name (like "cool topic") and i will create it and add it to the space')
        else:
            if evt.sender in self.config["admins"] or evt.sender in self.config["mods"]:
                try:
                    sanitized_name = re.sub(r"[^a-zA-Z0-9]", '', roomname).lower()
                    invitees = self.config['invitees']
                    parent_room = self.config['parent_room']
                    ## homeserver is derived from maubot's client instance since this is the user that will create the room
                    server = self.client.parse_user_id(self.client.mxid)[1]
                    # set bot PL higher than admin so we can kick old admins if needed
                    pl_override = {"users": {self.client.mxid: 1000}}
                    for u in self.config['admins']:
                        pl_override["users"][u] = 100
                    pl_json = json.dumps(pl_override)

                    mymsg = await evt.respond(f"creating {sanitized_name}, give me a minute...")
                    #self.log.info(mymsg)
                    room_id = await self.client.create_room(alias_localpart=sanitized_name, name=roomname,
                            invitees=invitees, power_level_override=pl_override)

                    await evt.respond(f"updating room states...", edits=mymsg)
                    parent_event_content = json.dumps({'auto_join': False, 'suggested': False, 'via': [server]})
                    child_event_content = json.dumps({'canonical': True, 'via': [server]})
                    join_rules_content = json.dumps({'join_rule': 'restricted', 'allow': [{'type': 'm.room_membership',
                        'room_id': parent_room}]})

                    await self.client.send_state_event(parent_room, 'm.space.child', parent_event_content, state_key=room_id)
                    await self.client.send_state_event(room_id, 'm.space.parent', child_event_content, state_key=parent_room)
                    await self.client.send_state_event(room_id, 'm.room.join_rules', join_rules_content, state_key="")

                    if self.config["encrypt"]:
                        encryption_content = json.dumps({"algorithm": "m.megolm.v1.aes-sha2"})

                        await self.client.send_state_event(room_id, 'm.room.encryption', encryption_content,
                                                           state_key="")
                        await evt.respond(f"encrypting room...", edits=mymsg)

                    await evt.respond(f"room created and updated, alias is #{sanitized_name}:{server}", edits=mymsg)


                except Exception as e:
                    await evt.respond(f"i tried, but something went wrong: \"{e}\"", edits=mymsg)
            else:
                await evt.reply("you're not the boss of me!")


    #need to somehow regularly fetch and update the list of room ids that are associated with a given space
    #to track events within so that we are actually only paying attention to those rooms

    ## loop through each room and report people who are "guests" (in the room, but not members of the space)
    @community.subcommand("guests", help="generate a list of members in a room who are not members of the parent space")
    @command.argument("room", required=False)
    async def get_guestlist(self, evt: MessageEvent, room: str) -> None:
        space_members_obj = await self.client.get_joined_members(self.config["parent_room"])
        space_members_list = space_members_obj.keys()
        room_id = None
        if room:
            if room.startswith('#'):
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
            await evt.reply(f"<b>Guests in this room are:</b><br /> \
                    {'<br />'.join(guest_list)}", allow_html=True)
        except Exception as e:
            await evt.respond(f"something went wrong: {e}")


    @classmethod
    def get_db_upgrade_table(cls) -> None:
        return upgrade_table

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
