# Community Bot
[![Chat on Matrix](https://img.shields.io/badge/chat_on_matrix-%23dev:mssj.me-green)](https://matrix.to/#/#dev:mssj.me)

a maubot plugin that helps administrators of communities on matrix, based on the concept of a matrix space. you may want
to leverage [join](https://github.com/williamkray/maubot-join) to ensure your bot doesn't end up somewhere it's not
supposed to be.

# should i use this?

why does this exist? there are some great tools out there already that do probably a much better job at combatting spam
and abuse on matrix, like [Draupnir](https://github.com/the-draupnir-project/Draupnir). this plugin might make sense for
you if:

- you're more interested in basic community management tools (like room creation, user activity tracking, etc)
- you already are running Maubot, or plan to
- you're afraid of mjolnir/draupnir for some reason
- you just really love python and want to contribute to this project

my opinion is that your community should probably be configured with the following restrictions to best align
with this plugin's capabilities:

- your Space is invite-only
- most rooms are join-restricted to only allow members of your space
- you have a smaller subset of rooms which are publicly facing, where users can join freely and ask admins to be added
  to the space

by following this structure, you reduce the amount of surface area you have to spend time defending against spam and
implementing censorship rules.

if that doesn't sound like how you want to structure your online community, you might be better off using something like
Draupnir or Mjolnir.

# features

please read through the comments in the `base-config.yaml` for more thorough explanations, but this covers the high
points.

## greet new users on joining a room

configure your bot to send a custom greeting to users whenever they join a room! configuration file provides a greeting
map (define multiple greetings each with an identifier) and then a configuration of which rooms to greet users in, and
which greeting message the bot should send them.

Configure a `notification_room` to receive messages when someone joins one of the greeting rooms. If you just want
notifications (perhaps when someone joins the space, where the bot likely cannot send a greeting anyway) set the
greeting name to `'none'` in the greeting map, and the bot will skip the greeting and send a notification to your
notification room.

## activity tracking and reporting

tracks the last message timestamp of a user across any room that the bot is in, and generates a simple report. intended
to be used to boot people from a matrix space and all space rooms after a period of inactivity (prune inactive users)
with the `purge` subcommand.

supports simple threshold configuration and the option to also track "reaction" activity. 

you can also exempt users from showing as "inactive" in the report by setting their ignore status with the `ignore` and
`unignore` subcommands, e.g. `!community ignore @takinabreak:fromthis.group`. this is helpful to avoid accidentally
purging admin accounts, backup accounts, rarely used bots, etc.

`sync` subcommand will actively sync your space member list with the database to track active members properly. new
members to the space automatically trigger a sync, as do most other commands. this command is mostly deprecated but you
may want to run it just to see what it does.

generate a report with the `report` subcommand (i.e. `!community report`) to see your inactive users. 

## user management

purge inactive users with the `purge` subcommand (i.e. `!community purge`).

kick an individual user from your space and all child rooms, regardless of activity status, with the `kick` subcommand
(e.g. `!community kick @malicious:user.here`). this is useful in communities built on the concept of private (invite
only) matrix spaces.

if you want more sever action, use the `ban` and `unban` subcommands to ban users from all rooms in the space (this action
will automatically kick them from those rooms as well). if you've made a mistake, use the unban option, but they will
need to rejoin all rooms themselves or be re-invited.

if configured with the `redact_on_ban` setting, banning a user from your space will also queue up to their last 100 messages in each room for redaction. if not, you can redact their messages in each individual room using the `!community redact` command.

use the `guests` subcommand to see who is in a room but NOT a member of the parent space (invited guests) e.g.
`!community guests #myroom:alias.here`.

## public banlist support

initial support for public banlists (as used by other tools like mjolnir/draupnir) is here! this bot leverages
banlists in read-only mode, just have your bot join one of these banlist rooms, and it will cross reference new room
members against these lists and immediately ban offenders. there is no intention to add new policy creation features
to this bot, as those concepts are probably best left to more featureful tools.

## admin/moderator management

set consistent power levels across all your rooms for your community administrators! the config defines a list of both
admins and moderators (admins have a Power Level of 100, mods have PL50). running the setpower subcommand (i.e.
`!community setpower`) will roll through all rooms in the space (including the space itself) and attempt to true-up user
permissions to match. if you are running legacy rooms not managed by the bot, and the bot does not have permission to
send power-level state events to the room, it will return a list for you to handle manually. users who have a PL greater
than 0 and are not listed as either an admin or moderator will be removed from the permission list, effectively
returning their power to whatever the room default is (usually 0).

## room creation

use the `createroom` subcommand to create a new room according to your preferences, and join it into the parent space.
include the `--encrypt` flag in your command to encrypt the room even if the default configuration is to create rooms
unencrypted.

will attempt to sanitize the room name and assign a room alias automatically. the bot user will be assigned very high
power level (1000) and set an admin power level (100) to plugin administrators, 50 to moderators. this ensures that the
bot is still able to manage room admins. the bot will also invite other users to these new rooms as configured.

rooms created by the bot will have join restriction limited to members of the space.

## room archival and replacement

use the `archive` subcommand to archive a room. this will remove the room from the parent space, remove all room aliases, and add a tombstone event to indicate the room is archived

use the `replaceroom` subcommand to replace an existing room with a new one. this is useful when:
- room members have power levels that cannot be corrected, or room members you cannot kick out
- you need to revert encryption settings
- you want to start fresh with a new room while preserving the old room's name and aliases

the replacement process will create a new room with the same name and avatar, transfer all room aliases to the new room, and archive the old room with a pointer to the new room. the new room will have standard join rules that restrict membership to space members. this logic is a little clunky, but it seems to work.

## get room ID

sometimes you need to know a rooms identifier, but if the room has an alias associated with it not all clients make it
easy (or possible) to find. this subcommand (`!community roomid`) can be used to return the room id that a room alias
points to. with no argument passed, it will return the current room's ID, or you can pass it an alias (e.g. `!community
roomid #whatisthisroom:myserver.tld`).

## message redaction

the bot can be configured to redact messages automatically to protect your users. set `censor` to either `true`,
`false`, or a list of room IDs to enable censorship in.

set `censor_files` to have the bot immediately redact file uploads in the censored rooms. define trigger words in
`censor_wordlist` to flag messages for automatic redaction.

please keep in mind that wordlist-based censorship is problematic and may redact false positives. writing a matching
algorithm that is perfect is impossible. consider configuring your community such that censorship need only be applied
in a limited subset of rooms.

# installation

install this like any other maubot plugin: zip the contents of this repo into a file and upload via the web interface,
or use the `mbc` utility to package and upload to your maubot server. 

be sure to give your bot permission to kick people from all rooms, otherwise management features will not work!
