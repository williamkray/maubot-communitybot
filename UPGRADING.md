# upgrade notes

version-specific notes for upgrading between releases, newest first. the plugin's database upgrades
automatically on first start and the upgrade is **one-directional** — always back up your database
before upgrading, and don't downgrade to an older build once you've run a newer one (maubot will
refuse to load a plugin whose known schema is older than the database's current version).

## v0.5

this line of releases adds nested-space (subspace) support and a pile of quality-of-life refinements.
highlights:

- **nested spaces (subspaces)** — organize your community into spaces-within-spaces. every space-wide
  operation (power-level sync, censorship, cross-room bans/kicks, inactivity purges, activity tracking,
  the `doctor` diagnostic) traverses the whole tree to any depth. build it with `!community space
  create`, inspect it with `!community space list`, rearrange with `!community room move`, and hand a
  user power over just one subspace with `!community space delegate` / `undelegate`.
- **multi-word names** — `!community space create` now accepts names with spaces, like `room create`
  always has.
- **nest rooms under a subspace** — `!community room create <name> --under <subspace>` drops the new
  room into a specific subspace instead of the top-level space.
- **community-joinable subspaces** — newly created subspaces default to a `restricted` join rule, so
  vetted community members can join them without an explicit invite (matching how managed rooms
  already behave).
- **`!community doctor`** — audit the bot's permissions across the whole space tree to spot problems.
- **rate-limit resilience** — homeserver-mutating calls retry on 429s; tune the pacing with the
  `sleep` config value.

these changes are additive — existing commands keep working and there's no manual migration to do. the
database continues to auto-upgrade (now at schema v7) on first start.

## v0.4

new features in this release:

- **emoji-reaction crowd moderation** — let members flag messages with a reaction (see the crowd
  moderation section in the README).
- **leave/kick/ban notifications** — the notification room now reports departures, not just joins.
- **`use_community_slug` toggle** — create room aliases without the `-<slug>` suffix.
- **community events (EXPERIMENTAL)** — see the events section in the README. this feature is incomplete
  and has known unresolved issues; it is included so it can be developed further, but is **not
  recommended for production use** yet.

this release upgrades the plugin database to schema **v6**. the upgrade runs automatically on
first start and is one-directional — as always, back up your database before upgrading. (note:
maubot will refuse to load a plugin whose known schema is *older* than the database's current
version, so do not downgrade to an earlier build once you've run v0.4.)

## v0.3

New functionality to support room v12 and newer has been added, as well as some significant restructuring of the code
and commands! v0.3.0 is potentially a breaking change, please make a backup of your old bot configuration and database
as necessary before updating in case anything goes horribly wrong. i take no responsibility.

commands are now broken up into more logical groupings, so instead of `!community createroom` it's `!community room
create`, etc. helpful usage messages are usually passed if you do things wrong, so this shouldn't be too complicated.

## v0.2

if you are upgrading from an earlier version to v0.2.0, please note that the user permission model has changed to be easier to manage, but will require some intervention.

statically defined `admins` and `moderators` in the config will no longer be used. instead, user permissions in rooms will be inherited from the parent space or room, and changes will cascade to all child rooms.

to migrate, ensure your bot is an admin of the parent space and use the `!community sync` command to make users in your admin and moderator lists appropriately leveled in that parent space. this will also clear out these lists to prepare for deprecation in a later version. you may want to run `!community setpower` to update your child rooms if there are significant changes.
