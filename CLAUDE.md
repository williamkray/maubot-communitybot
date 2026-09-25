# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A [maubot](https://github.com/maubot/maubot) plugin for Matrix space administration. Targets small, invite-only communities using Matrix spaces — not a spam/abuse tool (Draupnir/Mjolnir is recommended for that).

Plugin ID: `org.jobmachine.communitybot` | Version tracked in `maubot.yaml`

## Commands

```bash
# Run all tests (requires mautrix/maubot installed — use Docker if not available locally)
pytest tests/ -v

# Run a single test module
pytest tests/test_message_utils.py -v

# Run a single test function
pytest tests/test_user_utils.py::TestUserUtils::test_ban_user -v

# Run with coverage
pytest tests/ --cov=community

# Run tests via Docker (Python 3.12 required — f-string nesting in diagnostic_utils.py)
docker run --rm -v "$PWD":/app -w /app python:3.12-slim sh -c \
  "pip install --quiet mautrix maubot asyncpg pytest pytest-asyncio && pytest tests/ -v"
```

No build step — package as ZIP and upload via maubot web interface, or use `mbc` CLI.

### Known pre-existing test failures (not caused by code changes)

- `test_bot_commands` / `test_bot_events` — all fail: `CommunityBot()` requires 10 constructor args; tests instantiate it bare. Pre-existing infra issue.
- `test_user_utils::test_check_if_banned_success` — mock setup mismatch with current `check_if_banned` implementation.
- `test_report_utils::test_generate_activity_report_*` — tests expect a dict return value; actual function signature differs.

## Architecture

**Entry point**: `community/__init__.py` exports `CommunityBot` (the `main_class` in `maubot.yaml`).

**`community/bot.py`** (~3500 lines) is the monolithic main class containing:
- Command routing via maubot's `@command` decorators, organized as `!community <group> <subcommand>`
- Event handlers (`@event`) for joins, leaves, messages (censorship), and state changes (power level sync, banlist updates)
- `_redaction_loop()` background task that polls `redaction_tasks` DB table every minute
- Direct database access for activity tracking, redaction queuing, and verification states

**`community/helpers/`** — independent, testable modules extracted from bot.py:
- `message_utils.py` — censorship wordlist matching, room name/slug generation
- `room_utils.py` — alias validation, room version detection, power level queries
- `user_utils.py` — banlist cross-referencing, multi-room ban operations, permission checks
- `database_utils.py` — activity timestamps, redaction queue, verification state CRUD
- `report_utils.py` — activity report formatting and chunking for message size limits
- `decorators.py` — `@require_permission`, `@require_parent_room`, `@handle_errors`
- `room_creation_utils.py` — opinionated room/space creation with join restrictions
- `diagnostic_utils.py` — bot health/permission auditing
- `response_builder.py`, `config_manager.py`, `common_utils.py`, `event_utils.py`, `base_command_handler.py`

**`community/db.py`** — database upgrade table (3 versions):
- v1: `user_events` (activity tracking)
- v2: `redaction_tasks` (queued redactions)
- v3: `verification_states` (human verification flow)

## Key Concepts

**Parent space**: The `parent_room` config key is the source of truth for community membership. Power level changes there cascade to child rooms via `sync_power_levels`. Commands that affect "all rooms" operate on child rooms of this space.

**Command structure**: All commands are `!community <group> <subcommand>`. v0.3 restructured commands — old forms (e.g., `!community createroom`) no longer exist.

**Verification flow**: When `check_if_human` is enabled, new joiners receive a DM with a random phrase from `verification_phrases`. They must reply in DM within `verification_attempts` tries. State is stored in `verification_states` table.

**Banlist integration**: Configured via `banlists` (list of room aliases). `check_if_banned()` cross-references user against all banlist rooms. `proactive_banning: true` triggers auto-ban when a user is added to any subscribed banlist.

**Room version matters**: Modern room versions (12+) handle power levels differently — creators have unlimited power. `is_modern_room_version()` and `user_has_unlimited_power()` in `room_utils.py` handle this distinction.

## Matrix Protocol: Power Level Rules by Room Version

Before touching any power level logic, understand these homeserver-enforced rules:

**Room v12+ (modern):**
- Creators have structurally infinite power — no numeric PL is assigned to them.
- Creators **must not appear** in `content.users` of any `m.room.power_levels` event. Doing so returns `M_UNKNOWN: Creator user must not appear in content.users`.
- Non-creators can hold any numeric PL.
- Spec: https://spec.matrix.org/v1.18/rooms/v12/

**Legacy rooms (pre-v12):**
- No special creator concept. All power is numeric.
- PLs can be any integer — there is no cap of 100. A bot could legitimately hold PL 1000 in a legacy room.
- You **cannot set any PL higher than your own current PL** in that room. A bot with PL 100 cannot set itself or anyone else above 100 in that room, even if it has higher PL elsewhere.
- Each room's PL is independent — a bot may have PL 1000 in one room and PL 100 in another.

**Key implication for `setpower`:** When propagating PLs from a legacy parent to legacy child rooms, always use each child room's current bot PL as the ceiling. Never hardcode 1000 or any value higher than the bot's actual PL in that specific room.

## Testing

Tests use `pytest` with `asyncio` support. All Matrix API calls are mocked with `unittest.mock` (`Mock`, `AsyncMock`). Tests are organized to mirror the helper module structure — one test file per helper.
