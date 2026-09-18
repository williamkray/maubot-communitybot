from __future__ import annotations

from mautrix.util.async_db import UpgradeTable, Connection

upgrade_table = UpgradeTable()

@upgrade_table.register(description="Table initialization")
async def upgrade_v1(conn: Connection) -> None:
    await conn.execute(
            """CREATE TABLE user_events (
                mxid TEXT PRIMARY KEY,
                last_message_timestamp BIGINT NOT NULL,
                ignore_inactivity INT
            )"""
    )

@upgrade_table.register(description="Include message redaction tracking")
async def upgrade_v2(conn: Connection) -> None:
    await conn.execute(
            """CREATE TABLE redaction_tasks (
                event_id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL
            )"""
    )

@upgrade_table.register(description="Add verification states table")
async def upgrade_v3(conn: Connection) -> None:
    await conn.execute(
            """CREATE TABLE verification_states (
                dm_room_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                target_room_id TEXT NOT NULL,
                verification_phrase TEXT NOT NULL,
                attempts_remaining INTEGER NOT NULL,
                required_power_level INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
    )

@upgrade_table.register(description="Add community_events table for event tracking")
async def upgrade_v4(conn: Connection) -> None:
    await conn.execute(
            """CREATE TABLE community_events (
                room_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                event_start_ts BIGINT NOT NULL,
                event_end_ts BIGINT,
                location TEXT,
                host_id TEXT NOT NULL,
                organizers TEXT NOT NULL DEFAULT '[]',
                extra_links TEXT NOT NULL DEFAULT '[]',
                created_ts BIGINT NOT NULL,
                description_event_id TEXT,
                description_room_id TEXT
            )"""
    )
    await conn.execute(
            """CREATE TABLE event_rsvps (
                event_room_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                rsvp_status TEXT NOT NULL,
                plus_one INT NOT NULL DEFAULT 0,
                updated_ts BIGINT NOT NULL,
                PRIMARY KEY (event_room_id, user_id)
            )"""
    )
    await conn.execute(
            "CREATE INDEX idx_community_events_description_event_id ON community_events(description_event_id)"
    )
    await conn.execute(
            "CREATE INDEX idx_community_events_start_ts ON community_events(event_start_ts)"
    )


@upgrade_table.register(description="Add timezone column to community_events")
async def upgrade_v5(conn: Connection) -> None:
    await conn.execute(
            "ALTER TABLE community_events ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"
    )


@upgrade_table.register(description="Add message report tracking for crowd moderation")
async def upgrade_v6(conn: Connection) -> None:
    # tracks per-user reports (emoji reactions) against a message so that
    # crowd-moderation counts survive restarts. reported_at is the reaction's
    # origin_server_ts (milliseconds) and is used to purge stale reports.
    await conn.execute(
            """CREATE TABLE message_reports (
                room_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                reporter TEXT NOT NULL,
                reported_at BIGINT NOT NULL,
                PRIMARY KEY (room_id, event_id, reporter)
            )"""
    )


@upgrade_table.register(description="Event guest counts, per-event guest caps, and reaction tracking")
async def upgrade_v7(conn: Connection) -> None:
    # Per-invitee additional-guest count (replaces the old 0/1 plus_one).
    await conn.execute(
            "ALTER TABLE event_rsvps ADD COLUMN guest_count INTEGER NOT NULL DEFAULT 0"
    )
    # Preserve any existing plus_one data as a guest_count of 0 or 1.
    await conn.execute("UPDATE event_rsvps SET guest_count = plus_one")
    # Per-event cap on additional guests per invitee: -1 = unlimited, 0 = none.
    await conn.execute(
            "ALTER TABLE community_events ADD COLUMN max_additional_guests INTEGER NOT NULL DEFAULT 1"
    )
    # Track individual RSVP reaction events so redactions (un-reacting) update
    # the count, and so RSVP state can be recomputed from the active reactions.
    await conn.execute(
            """CREATE TABLE event_reactions (
                reaction_event_id TEXT PRIMARY KEY,
                event_room_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                created_ts BIGINT NOT NULL
            )"""
    )
    await conn.execute(
            "CREATE INDEX idx_event_reactions_event_user ON event_reactions(event_room_id, user_id)"
    )
