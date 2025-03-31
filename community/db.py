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
