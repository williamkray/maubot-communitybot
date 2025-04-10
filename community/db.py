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
