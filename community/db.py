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
