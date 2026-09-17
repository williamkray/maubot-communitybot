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

@upgrade_table.register(description="Add message report tracking for crowd moderation")
async def upgrade_v4(conn: Connection) -> None:
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
