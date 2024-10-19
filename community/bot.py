# kickbot - a maubot plugin to track user activity and remove inactive users from rooms/spaces.

from typing import Awaitable, Type, Optional, Tuple
import json
import time
import re
import fnmatch

from mautrix.client import Client, InternalEventType, MembershipEventDispatcher, SyncStream
from mautrix.types import (Event, StateEvent, EventID, UserID, FileInfo, EventType,
                            MediaMessageEventContent, ReactionEvent, RedactionEvent, RoomID,
                            RoomAlias, PowerLevelStateEventContent, MessageType)
from mautrix.errors import MNotFound
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event
BAN_STATE_EVENT = EventType.find("m.policy.rule.user", EventType.Class.STATE)

# database table related things
from .db import upgrade_table



class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("admins")
        helper.copy("moderators")
        helper.copy("parent_room")
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


class CommunityBot(Plugin):

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.client.add_dispatcher(MembershipEventDispatcher)


    async def do_sync(self) -> None:
        if not self.config["track_users"]:
            return "user tracking is disabled"

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
            AND (ignore_inactivity < 1 OR ignore_inactivity IS NULL)
            """
        kick_q = """
            SELECT mxid FROM user_events WHERE last_message_timestamp <= $1
            AND (ignore_inactivity < 1 OR ignore_inactivity IS NULL)
            """
        ignored_q = """
            SELECT mxid FROM user_events WHERE ignore_inactivity = 1
            """
        warn_inactive_results = await self.database.fetch(warn_q, warn_days_ago, kick_days_ago)
        kick_inactive_results = await self.database.fetch(kick_q, kick_days_ago)
        ignored_results = await self.database.fetch(ignored_q)
        report = {}
        report["warn_inactive"] = [ row["mxid"] for row in warn_inactive_results ] or ["none"]
        report["kick_inactive"] = [ row["mxid"] for row in kick_inactive_results ] or ["none"]
        report["ignored"] = [ row["mxid"] for row in ignored_results ] or ["none"]

        return report

    def flag_message(self, msg):
        if msg.content.msgtype in [MessageType.FILE, MessageType.IMAGE, MessageType.VIDEO]:
            return self.config['censor_files']

        for w in self.config['censor_wordlist']:
            try:
                if bool(re.search(w, msg.content.body, re.IGNORECASE)):
                    #self.log.debug(f"DEBUG message flagged for censorship")
                    return True
                else:
                    pass
            except Exception as e:
                self.log.error(f"Could not parse message for flagging: {e}")

    def flag_instaban(self, msg):
        for w in self.config['censor_wordlist_instaban']:
            try:
                if bool(re.search(w, msg.content.body, re.IGNORECASE)):
                    #self.log.debug(f"DEBUG message flagged for instaban")
                    return True
                else:
                    pass
            except Exception as e:
                self.log.error(f"Could not parse message for flagging: {e}")


    def censor_room(self, msg):
        if isinstance(self.config['censor'], bool):
            #self.log.debug(f"DEBUG message will be redacted because censoring is enabled")
            return self.config['censor']
        elif isinstance(self.config['censor'], list):
            if msg.room_id in self.config['censor']:
                #self.log.debug(f"DEBUG message will be redacted because censoring is enabled for THIS room")
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
                self.log.error(f"Bot must be in {list_id} before attempting to use it as a banlist.")
                pass

            #self.log.debug(f"DEBUG looking up state in {list_id}")
            list_state = await self.client.get_state(list_id)
            #self.log.debug(f"DEBUG state found: {list_state}")
            try:
                user_policies = list(filter(lambda p : p.type.t=='m.policy.rule.user', list_state))
                #self.log.debug(f"DEBUG user policies found: {user_policies}")
            except Exception as e:
                self.log.error(e)

            for rule in user_policies:
                #self.log.debug(f"Checking match of user {userid} in banlist {l} for {rule['content']}")
                try:
                    if bool(fnmatch.fnmatch(userid, rule["content"]["entity"])) and \
                            bool(re.search('ban$', rule["content"]["recommendation"])):
                        #self.log.debug(f"DEBUG user {userid} matches ban rule {rule['content']['entity']}!")
                        return True
                    else:
                        pass
                except Exception as e:
                    self.log.debug(f"Found something funny in the banlist {list_id} for {rule['content']}: {e}")
                    pass
        # if we haven't exited by now, we must not be banned!
        return is_banned

    async def ban_this_user(self, user, reason="banned", all_rooms=False):
        #self.log.debug(f"DEBUG getting list of rooms")
        roomlist = await self.get_space_roomlist()
        # don't forget to kick from the space itself
        roomlist.append(self.config["parent_room"])
        #self.log.debug(f"DEBUG list of rooms acquired")
        ban_event_map = {'ban_list':{}, 'error_list':{}}

        ban_event_map['ban_list'][user] = []
        #self.log.debug(f"DEBUG banning {user} from rooms...")
        for room in roomlist:
            try:
                roomname = None
                roomnamestate = await self.client.get_state_event(room, 'm.room.name')
                roomname = roomnamestate['name']

                # ban user even if they're not in the room!
                if all_rooms:
                    pass
                else:
                    await self.client.get_state_event(room, EventType.ROOM_MEMBER, user)

                await self.client.ban_user(room, user, reason=reason)
                if roomname:
                    ban_event_map['ban_list'][user].append(roomname)
                else:
                    ban_event_map['ban_list'][user].append(room)
                time.sleep(0.5)
            except MNotFound:
                pass
            except Exception as e:
                self.log.warning(e)
                ban_event_map['error_list'][user] = []
                ban_event_map['error_list'][user].append(roomname or room)
        
        return ban_event_map

    async def get_banlist_roomids(self):
        banlist_roomids = []
        for l in self.config['banlists']:
            #self.log.debug(f"DEBUG getting banlist {l}")
            if l.startswith('#'):
                try:
                    l_id = await self.client.resolve_room_alias(l)
                    list_id = l_id["room_id"]
                    #self.log.debug(f"DEBUG banlist id resolves to: {list_id}")
                except:
                    evt.reply("i don't recognize that list, sorry")
                    return
            else:
                list_id = l

            banlist_roomids.append(list_id)

        return banlist_roomids


    @event.on(BAN_STATE_EVENT)
    async def check_ban_event(self, evt:StateEvent) -> None:
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
                self.log.debug(f"DEBUG new ban rule found: {entity} should have action {recommendation}")
                if bool(re.search(r"[*?]", entity)):
                    self.log.debug(f"DEBUG ban rule appears to be glob pattern, skipping proactive measures.")
                    return
                if bool(re.search('ban$', recommendation)):
                    await self.ban_this_user(entity)
            except Exception as e:
                self.log.error(e)

        
    @event.on(InternalEventType.JOIN)
    async def newjoin(self, evt:StateEvent) -> None:
        if evt.source & SyncStream.STATE:
            return
        else:
            on_banlist = await self.check_if_banned(evt.sender)
            if on_banlist:
                #self.log.debug(f"DEBUG user is on banlist!")
                # ban this account in managed rooms, don't bother with anything else
                await self.ban_this_user(evt.sender)
                return
            # passive sync of tracking db
            if evt.room_id == self.config['parent_room']:
                await self.do_sync()
            # greeting activities
            room_id = str(evt.room_id)
            if room_id in self.config["greeting_rooms"]:
                # just in case we got here even if the person is on the banlists
                if on_banlist:
                    return
                greeting_map = self.config['greetings']
                greeting_name = self.config['greeting_rooms'][room_id]
                nick = self.client.parse_user_id(evt.sender)[0]
                pill = '<a href="https://matrix.to/#/{mxid}">{nick}</a>'.format(mxid=evt.sender, nick=nick)
                if greeting_name != "none":
                    greeting = greeting_map[greeting_name].format(user=pill)
                    await self.client.send_notice(evt.room_id, html=greeting) 
                else:
                    pass
                if self.config["notification_room"]:
                    roomnamestate = await self.client.get_state_event(evt.room_id, 'm.room.name')
                    roomname = roomnamestate['name']
                    notification_message = self.config['join_notification_message'].format(user=evt.sender, 
                                                                                      room=roomname)
                    await self.client.send_notice(self.config["notification_room"], html=notification_message)

    @event.on(EventType.ROOM_MESSAGE)
    async def update_message_timestamp(self, evt: MessageEvent) -> None:
        power_levels = await self.client.get_state_event(evt.room_id, EventType.ROOM_POWER_LEVELS)
        user_level = power_levels.get_user_level(evt.sender)
        #self.log.debug(f"DEBUGDEBUG user {evt.sender} has power level {user_level}")
        if self.flag_message(evt):
            # do we need to redact?
            if evt.sender not in self.config['admins'] and \
                    evt.sender not in self.config['moderators'] and \
                    user_level < self.config['uncensor_pl'] and \
                    evt.sender != self.client.mxid and \
                    self.censor_room(evt):
                try:
                    await self.client.redact(evt.room_id, evt.event_id, reason="message flagged")
                except Exception as e:
                    self.log.error(f"Flagged message could not be redacted: {e}")
        if evt.content.msgtype in {MessageType.TEXT, MessageType.NOTICE, MessageType.EMOTE}:
            if self.flag_instaban(evt):
                # do we need to redact?
                if evt.sender not in self.config['admins'] and \
                        evt.sender not in self.config['moderators'] and \
                        user_level < self.config['uncensor_pl'] and \
                        evt.sender != self.client.mxid and \
                        self.censor_room(evt):
                    try:
                        await self.client.redact(evt.room_id, evt.event_id, reason="message flagged")
                    except Exception as e:
                        self.log.error(f"Flagged message could not be redacted: {e}")
                        
                    await self.ban_this_user(evt.sender, all_rooms=True)

        if not self.config["track_messages"] or not self.config["track_users"]:
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
        if not self.config["track_reactions"] or not self.config["track_users"]:
            pass
        else:
            q = """
                INSERT INTO user_events(mxid, last_message_timestamp) 
                VALUES ($1, $2)
                ON CONFLICT(mxid)
                DO UPDATE SET last_message_timestamp=$2
            """
            await self.database.execute(q, evt.sender, evt.timestamp)

    @command.new("community", help="manage rooms and members of a space")
    async def community(self) -> None:
        pass

    @community.subcommand("bancheck", help="check subscribed banlists for a user's mxid")
    @command.argument("mxid", "full matrix ID", required=True)
    async def check_banlists(self, evt: MessageEvent, mxid: UserID) -> None:
        ban_status = await self.check_if_banned(mxid)
        await evt.reply(f"user on banlist: {ban_status}")


    @community.subcommand("sync", help="update the activity tracker with the current space members \
            in case they are missing")
    async def sync_space_members(self, evt: MessageEvent) -> None:
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
            if not self.config["track_users"]:
                await evt.respond("user tracking is disabled")
                return

            results = await self.do_sync()

            added_str = "<br />".join(results['added'])
            dropped_str = "<br />".join(results['dropped'])
            await evt.respond(f"Added: {added_str}<br /><br />Dropped: {dropped_str}", allow_html=True)
        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("ignore", help="exclude a specific matrix ID from inactivity tracking")
    @command.argument("mxid", "full matrix ID", required=True)
    async def ignore_inactivity(self, evt: MessageEvent, mxid: UserID) -> None:
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
            if not self.config["track_users"]:
                await evt.reply("user tracking is disabled")
                return

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
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
            if not self.config["track_users"]:
                await evt.reply("user tracking is disabled")
                return

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
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
            if not self.config["track_users"]:
                await evt.reply("user tracking is disabled")
                return

            sync_results = await self.do_sync()
            report = await self.generate_report()
            await evt.respond(f"<p><b>Users inactive for between {self.config['warn_threshold_days']} and \
                    {self.config['kick_threshold_days']} days:</b><br /> \
                    {'<br />'.join(report['warn_inactive'])} <br /></p>\
                    <p><b>Users inactive for at least {self.config['kick_threshold_days']} days:</b><br /> \
                    {'<br />'.join(report['kick_inactive'])} <br /></p> \
                    <p><b>Ignored users:</b><br /> \
                    {'<br />'.join(report['ignored'])}</p>", \
                    allow_html=True)


    @community.subcommand("purge", help='kick users for excessive inactivity')
    async def kick_users(self, evt: MessageEvent) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
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
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
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
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
            user = mxid
            msg = await evt.respond("starting the ban...")
            results_map = await self.ban_this_user(user, all_rooms=True)


            results = "the following users were kicked and banned:<p><code>{ban_list}</code></p>the following errors were \
                    recorded:<p><code>{error_list}</code></p>".format(ban_list=results_map['ban_list'],
                                                                      error_list=results_map['error_list'])
            await evt.respond(results, allow_html=True, edits=msg)
        
            # sync our database after we've made changes to room memberships
            await self.do_sync()

        else:
            await evt.reply("lol you don't have permission to do that")


    @community.subcommand("unban", help='unban a specific user from the community and all rooms')
    @command.argument("mxid", "full matrix ID", required=True)
    async def unban_user(self, evt: MessageEvent, mxid: UserID) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
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


    @community.subcommand("createroom", help="create a new room titled <roomname> and add it to the parent space. \
                          optionally include `--encrypt` to encrypt it regardless of the default settings.")
    @command.argument("roomname", pass_raw=True, required=True)
    async def create_that_room(self, evt: MessageEvent, roomname: str) -> None:
        if (roomname == "help") or len(roomname) == 0:
            await evt.reply('pass me a room name (like "cool topic") and i will create it and add it to the space. \
                            use `--encrypt` to ensure it is encrypted at creation time even if that isnt my default \
                            setting.')
        else:
            if evt.sender in self.config["admins"] or evt.sender in self.config["moderators"]:
                encrypted_flag_regex = re.compile(r'(\s+|^)-+encrypt(ed)?\s?')
                force_encryption = bool(encrypted_flag_regex.search(roomname))
                try:
                    if force_encryption:
                        roomname = encrypted_flag_regex.sub('', roomname)
                    sanitized_name = re.sub(r"[^a-zA-Z0-9]", '', roomname).lower()
                    invitees = self.config['invitees']
                    parent_room = self.config['parent_room']
                    ## homeserver is derived from maubot's client instance since this is the user that will create the room
                    server = self.client.parse_user_id(self.client.mxid)[1]
                    # set bot PL higher than admin so we can kick old admins if needed
                    pl_override = {"users": {self.client.mxid: 1000}}
                    for u in self.config['admins']:
                        pl_override["users"][u] = 100
                    for u in self.config['moderators']:
                        pl_override["users"][u] = 50
                    pl_json = json.dumps(pl_override)

                    mymsg = await evt.respond(f"creating {sanitized_name}, give me a minute...")
                    #self.log.info(mymsg)
                    room_id = await self.client.create_room(alias_localpart=sanitized_name, name=roomname,
                            invitees=invitees, power_level_override=pl_override)
                    time.sleep(0.5)

                    await evt.respond(f"updating room states...", edits=mymsg)
                    parent_event_content = json.dumps({'auto_join': False, 'suggested': False, 'via': [server]})
                    child_event_content = json.dumps({'canonical': True, 'via': [server]})
                    join_rules_content = json.dumps({'join_rule': 'restricted', 'allow': [{'type': 'm.room_membership',
                        'room_id': parent_room}]})

                    await self.client.send_state_event(parent_room, 'm.space.child', parent_event_content, state_key=room_id)
                    time.sleep(0.5)
                    await self.client.send_state_event(room_id, 'm.space.parent', child_event_content, state_key=parent_room)
                    time.sleep(0.5)
                    await self.client.send_state_event(room_id, 'm.room.join_rules', join_rules_content, state_key="")
                    time.sleep(0.5)

                    if self.config["encrypt"] or force_encryption:
                        encryption_content = json.dumps({"algorithm": "m.megolm.v1.aes-sha2"})

                        await self.client.send_state_event(room_id, 'm.room.encryption', encryption_content,
                                                           state_key="")
                        await evt.respond(f"encrypting room...", edits=mymsg)
                        time.sleep(0.5)

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

    @community.subcommand("roomid", help="return the matrix room ID of this, or a given, room")
    @command.argument("room", required=False)
    async def get_roomid(self, evt: MessageEvent, room: str) -> None:
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
        try:
            await evt.reply(f"Room ID is: {room_id}")
        except Exception as e:
            await evt.respond(f"something went wrong: {e}")

    @community.subcommand("setpower", help="set power levels according to the community configuration")
    async def set_powerlevels(self, evt: MessageEvent,) -> None:
        await evt.mark_read()
        if evt.sender in self.config["admins"]:
            msg = await evt.respond("truing up power levels, this could take a minute...")
            admins = self.config['admins']
            moderators = self.config['moderators']
            roomlist = await self.get_space_roomlist()
            # don't forget to include the space itself
            roomlist.append(self.config["parent_room"])
            success_list = []
            error_list = []
            adminpl = 100
            modpl = 50
            defaultpl = 0

            for room in roomlist:
                # need to get and evaluate the current state that contains powerlevels first
                current_pl = await self.client.get_state_event(room, 'm.room.power_levels')
                users = current_pl['users'].serialize()
                updated_user_map = dict(users)
                try:
                    roomname = None
                    roomnamestate = await self.client.get_state_event(room, 'm.room.name')
                    roomname = roomnamestate['name']
                except Exception as e:
                    self.log.warning(e)

                # update our powerlevel map values
                for user in admins:
                    updated_user_map[user] = adminpl
                for user in moderators:
                    updated_user_map[user] = modpl

                # revoke values for people no longer in the config
                for user in users.keys():
                    if ( user not in admins and 
                            user not in moderators and 
                            updated_user_map[user] > defaultpl and 
                            user != self.client.mxid ):
                        del updated_user_map[user]


                # and send the new state event back to the room
                new_pl = current_pl
                new_pl['users'] = updated_user_map
                try:
                    #self.log.debug(f"DEBUG sending finalized PL map to room {room}: {updated_user_map}")
                    await self.client.send_state_event(room, 'm.room.power_levels', new_pl)
                    success_list.append(roomname or room)
                except Exception as e:
                    self.log.warning(e)
                    error_list.append(roomname or room)

                time.sleep(0.5)

            results = "the following rooms were updated:<p><code>{success_list}</code></p>the following errors were \
                    recorded:<p><code>{error_list}</code></p>".format(success_list=success_list, error_list=error_list)
            await evt.respond(results, allow_html=True, edits=msg)
        
            # sync our database after we've made changes to room memberships
            await self.do_sync()

        else:
            await evt.reply("lol you don't have permission to do that")

    


    @classmethod
    def get_db_upgrade_table(cls) -> None:
        return upgrade_table

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
