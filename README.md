# Community Bot

a maubot plugin that helps administrators of communities on matrix, based on the concept of a matrix space. this bot
will attempt to track user activity in any room that it is in, so you may want to leverage
[join](https://github.com/williamkray/maubot-join) to ensure your bot doesn't end up somewhere it's not supposed to be.

# features

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

use the `guests` subcommand to see who is in a room but NOT a member of the parent space (invited guests) e.g.
`!community guests #myroom:alias.here`.

## room creation

use the `createroom` subcommand to create a new room according to your preferences, and join it into the parent space.
will attempt to sanitize the room name and assign a room alias automatically. the bot user will be assigned very high
power level (1000) and set an admin power level (100) to plugin administrators. this ensures that the bot is still able
to manage room admins. the bot will also invite other users to these new rooms as configured.

## get room ID

sometimes you need to know a rooms identifier, but if the room has an alias associated with it not all clients make it
easy (or possible) to find. this subcommand (`!community roomid`) can be used to return the room id that a room alias
points to. with no argument passed, it will return the current room's ID, or you can pass it an alias (e.g. `!community
roomid #whatisthisroom:myserver.tld`).

# installation

install this like any other maubot plugin: zip the contents of this repo into a file and upload via the web interface,
or use the `mbc` utility to package and upload to your maubot server. 

be sure to give your bot permission to kick people from all rooms, otherwise management features will not work!
