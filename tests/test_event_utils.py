"""Tests for community event utilities and event-management handlers.

Covers:
  A. Pure functions in community/helpers/event_utils.py (link CRUD, formatting, ICS).
  B. A real-sqlite test of the RSVP upsert (portability + plus_one preservation
     semantics) driven through CommunityBot.handle_event_rsvp.
  C. Handler tests for event_remove_link / event_edit_link / event_links.
"""

import json

import pytest
from unittest.mock import Mock, AsyncMock

from mautrix.types import ReactionEvent, RelationType

from community.bot import CommunityBot
from community.helpers import event_utils


# ---------------------------------------------------------------------------
# A. Pure-function tests
# ---------------------------------------------------------------------------

class TestAddLink:
    def test_add_new_link(self):
        out, added = event_utils.add_link([], "https://a.com", "Site A")
        assert added is True
        assert out == [{"label": "Site A", "url": "https://a.com"}]

    def test_default_label(self):
        out, added = event_utils.add_link([], "https://a.com")
        assert added is True
        assert out[0]["label"] == "Link"

    def test_dedupe_case_insensitive_and_trimmed_updates_label(self):
        links = [{"label": "Old", "url": "https://Example.com"}]
        out, added = event_utils.add_link(links, "  https://example.COM  ", "New")
        assert added is False
        assert len(out) == 1
        assert out[0]["url"] == "https://Example.com"  # original url preserved
        assert out[0]["label"] == "New"  # label updated

    def test_does_not_mutate_input(self):
        links = [{"label": "Old", "url": "https://a.com"}]
        out, _ = event_utils.add_link(links, "https://b.com", "B")
        assert links == [{"label": "Old", "url": "https://a.com"}]
        assert len(out) == 2

    def test_blank_label_falls_back_to_link(self):
        out, _ = event_utils.add_link([], "https://a.com", "   ")
        assert out[0]["label"] == "Link"


class TestRemoveLink:
    def _links(self):
        return [
            {"label": "First", "url": "https://one.com"},
            {"label": "Second", "url": "https://two.com"},
        ]

    def test_remove_by_index(self):
        out, removed = event_utils.remove_link(self._links(), "1")
        assert removed == {"label": "First", "url": "https://one.com"}
        assert out == [{"label": "Second", "url": "https://two.com"}]

    def test_remove_by_url_case_insensitive(self):
        out, removed = event_utils.remove_link(self._links(), "HTTPS://TWO.COM")
        assert removed["label"] == "Second"
        assert len(out) == 1

    def test_remove_by_label(self):
        out, removed = event_utils.remove_link(self._links(), "first")
        assert removed["url"] == "https://one.com"
        assert len(out) == 1

    def test_index_out_of_range(self):
        out, removed = event_utils.remove_link(self._links(), "9")
        assert removed is None
        assert len(out) == 2

    def test_index_zero_out_of_range(self):
        out, removed = event_utils.remove_link(self._links(), "0")
        assert removed is None

    def test_no_match(self):
        out, removed = event_utils.remove_link(self._links(), "nope")
        assert removed is None
        assert len(out) == 2

    def test_empty_selector(self):
        out, removed = event_utils.remove_link(self._links(), "")
        assert removed is None
        assert len(out) == 2


class TestEditLink:
    def _links(self):
        return [
            {"label": "First", "url": "https://one.com"},
            {"label": "Second", "url": "https://two.com"},
        ]

    def test_edit_url_by_index(self):
        out, edited = event_utils.edit_link(self._links(), "1", new_url="https://new.com")
        assert edited["url"] == "https://new.com"
        assert out[0]["url"] == "https://new.com"
        assert out[0]["label"] == "First"

    def test_edit_label_by_url(self):
        out, edited = event_utils.edit_link(
            self._links(), "https://two.com", new_label="Renamed"
        )
        assert edited["label"] == "Renamed"
        assert out[1]["label"] == "Renamed"

    def test_edit_both_by_label(self):
        out, edited = event_utils.edit_link(
            self._links(), "First", new_url="https://x.com", new_label="X"
        )
        assert edited == {"label": "X", "url": "https://x.com"}

    def test_edit_no_match(self):
        out, edited = event_utils.edit_link(self._links(), "nope", new_url="https://x.com")
        assert edited is None
        assert out == self._links()

    def test_edit_index_out_of_range(self):
        out, edited = event_utils.edit_link(self._links(), "5", new_label="X")
        assert edited is None

    def test_edit_does_not_mutate_input(self):
        links = self._links()
        event_utils.edit_link(links, "1", new_label="Changed")
        assert links[0]["label"] == "First"

    def test_edit_blank_values_ignored(self):
        out, edited = event_utils.edit_link(
            self._links(), "1", new_url="   ", new_label="  "
        )
        # nothing changed but a target was found
        assert edited["url"] == "https://one.com"
        assert edited["label"] == "First"


class TestFormatLinksList:
    def test_empty(self):
        assert event_utils.format_links_list([]) == "No links attached to this event."

    def test_numbered_html(self):
        out = event_utils.format_links_list(
            [
                {"label": "First", "url": "https://one.com"},
                {"label": "Second", "url": "https://two.com"},
            ]
        )
        assert out.startswith("1. ")
        assert "2. " in out
        assert '<a href="https://one.com">First</a>' in out
        assert "https://two.com" in out


class TestFormatEventDescriptionText:
    def test_includes_key_lines(self):
        out = event_utils.format_event_description_text(
            name="Party",
            start_ts=1_700_000_000_000,
            end_ts=1_700_003_600_000,
            location="The Park",
            host_id="@host:example.com",
            organizers=["@org:example.com"],
            description="Come along",
            extra_links=[{"label": "Info", "url": "https://info.com"}],
            room_id="!ev:example.com",
            timezone_str="UTC",
        )
        assert out.startswith("Party")
        assert "Host: @host:example.com" in out
        assert "Organizers: @org:example.com" in out
        assert "Location: The Park" in out
        assert "Come along" in out
        assert "Info: https://info.com" in out
        assert "RSVP with reactions:" in out
        assert "Event room: https://matrix.to/#/!ev:example.com" in out

    def test_minimal_no_optional_fields(self):
        out = event_utils.format_event_description_text(
            name="Minimal",
            start_ts=1_700_000_000_000,
            end_ts=None,
            location=None,
            host_id="@h:example.com",
            organizers=[],
            description=None,
            extra_links=[],
            room_id="!r:example.com",
        )
        assert "Host: @h:example.com" in out
        assert "Location:" not in out
        assert "Organizers:" not in out
        assert "Event room:" in out


class TestGenerateICS:
    def test_contains_required_fields(self):
        out = event_utils.generate_ics(
            name="Meetup; with, chars\nnewline",
            start_ts=1_700_000_000_000,
            end_ts=1_700_003_600_000,
            location="HQ",
            description="Desc",
            room_id="!room:example.com",
            uid_suffix="abc",
        )
        assert "BEGIN:VCALENDAR" in out
        assert "BEGIN:VEVENT" in out
        assert "DTSTAMP:" in out
        assert "DTSTART:" in out
        assert "DTEND:" in out
        assert "SUMMARY:" in out
        assert "END:VCALENDAR" in out
        # timezone-aware -> Z suffix on stamps
        assert "DTSTART:" in out and "Z" in out.split("DTSTART:")[1].split("\r\n")[0]
        # escaping of special chars in SUMMARY
        summary_line = [l for l in out.split("\r\n") if l.startswith("SUMMARY:")][0]
        assert "\\;" in summary_line and "\\," in summary_line and "\\n" in summary_line

    def test_end_defaults_when_missing(self):
        out = event_utils.generate_ics(
            name="NoEnd",
            start_ts=1_700_000_000_000,
            end_ts=None,
            location=None,
            description=None,
            room_id="!r:example.com",
            uid_suffix="x",
        )
        # DTEND still present (start + 1h fallback) and does not raise
        assert "DTEND:" in out


def test_exactly_one_format_event_topic_definition():
    """A duplicate definition was removed; assert there is exactly one."""
    import inspect

    src = inspect.getsource(event_utils)
    assert src.count("def format_event_topic(") == 1


# ---------------------------------------------------------------------------
# B. Real-database RSVP upsert test (portability + plus_one preservation)
# ---------------------------------------------------------------------------

EVENT_ROOM = "!eventroom:example.com"
DESC_EVENT_ID = "$descevent:example.com"
RSVP_SENDER = "@rsvper:example.com"


async def _make_db():
    from mautrix.util.async_db import Database
    from community.db import upgrade_table

    db = Database.create("sqlite:///:memory:", upgrade_table=upgrade_table)
    await db.start()
    # mautrix's sqlite :memory: pool can share process-global state across
    # Database instances, so start each test from a clean slate.
    await db.execute("DELETE FROM community_events")
    await db.execute("DELETE FROM event_rsvps")
    return db


def _make_rsvp_bot(db):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.database = db
    bot.client = Mock()
    bot.client.mxid = "@bot:example.com"
    bot.client.get_joined_members = AsyncMock(return_value={RSVP_SENDER: {}})
    bot.client.invite_user = AsyncMock()
    bot.is_user_in_parent_space = AsyncMock(return_value=True)
    bot.config = {"parent_room": "!parent:example.com"}
    return bot


def _make_reaction(key):
    evt = Mock(spec=ReactionEvent)
    evt.sender = RSVP_SENDER
    evt.room_id = "!descroom:example.com"
    evt.content = Mock()
    relates_to = Mock()
    relates_to.rel_type = RelationType.ANNOTATION
    relates_to.key = key
    relates_to.event_id = DESC_EVENT_ID
    evt.content.relates_to = relates_to
    return evt


async def _fetch_rsvp(db):
    return await db.fetchrow(
        "SELECT rsvp_status, plus_one FROM event_rsvps "
        "WHERE event_room_id = $1 AND user_id = $2",
        EVENT_ROOM,
        RSVP_SENDER,
    )


class TestRSVPUpsertRealDB:
    @pytest.mark.asyncio
    async def test_plus_one_semantics_sequence(self):
        db = await _make_db()
        try:
            # seed a community_events row keyed by description_event_id
            await db.execute(
                """INSERT INTO community_events
                   (room_id, name, event_start_ts, host_id, created_ts,
                    description_event_id)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                EVENT_ROOM,
                "Test Event",
                1_700_000_000_000,
                "@host:example.com",
                1_700_000_000_000,
                DESC_EVENT_ID,
            )
            bot = _make_rsvp_bot(db)

            # 👍 -> yes, +0
            assert await CommunityBot.handle_event_rsvp(bot, _make_reaction("👍")) is True
            row = await _fetch_rsvp(db)
            assert row["rsvp_status"] == "yes"
            assert row["plus_one"] == 0

            # ➕ -> yes, +1
            assert await CommunityBot.handle_event_rsvp(bot, _make_reaction("➕")) is True
            row = await _fetch_rsvp(db)
            assert row["rsvp_status"] == "yes"
            assert row["plus_one"] == 1

            # 🤔 -> maybe, plus_one PRESERVED (+1)
            assert await CommunityBot.handle_event_rsvp(bot, _make_reaction("🤔")) is True
            row = await _fetch_rsvp(db)
            assert row["rsvp_status"] == "maybe"
            assert row["plus_one"] == 1

            # ➖ -> status preserved (maybe), plus_one cleared (+0)
            assert await CommunityBot.handle_event_rsvp(bot, _make_reaction("➖")) is True
            row = await _fetch_rsvp(db)
            assert row["rsvp_status"] == "maybe"
            assert row["plus_one"] == 0
        finally:
            await db.stop()

    @pytest.mark.asyncio
    async def test_no_matching_event_returns_false(self):
        db = await _make_db()
        try:
            bot = _make_rsvp_bot(db)
            # no community_events row seeded -> not an RSVP
            assert await CommunityBot.handle_event_rsvp(bot, _make_reaction("👍")) is False
            row = await _fetch_rsvp(db)
            assert row is None
        finally:
            await db.stop()

    @pytest.mark.asyncio
    async def test_no_upsert_error_no_op_reaction(self):
        db = await _make_db()
        try:
            await db.execute(
                """INSERT INTO community_events
                   (room_id, name, event_start_ts, host_id, created_ts,
                    description_event_id)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                EVENT_ROOM,
                "Test Event",
                1_700_000_000_000,
                "@host:example.com",
                1_700_000_000_000,
                DESC_EVENT_ID,
            )
            bot = _make_rsvp_bot(db)
            # a non-RSVP key is not consumed
            assert await CommunityBot.handle_event_rsvp(bot, _make_reaction("🎉")) is False
        finally:
            await db.stop()


# ---------------------------------------------------------------------------
# C. Handler tests for link management commands
# ---------------------------------------------------------------------------

def _make_event_row(links):
    return {
        "room_id": EVENT_ROOM,
        "name": "Test Event",
        "extra_links": json.dumps(links),
        "host_id": "@host:example.com",
        "organizers": "[]",
    }


def _make_link_bot(event_row):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.database = Mock()
    bot.database.execute = AsyncMock()
    bot._resolve_event_room = AsyncMock(return_value=(EVENT_ROOM, None))
    bot._get_event_row = AsyncMock(return_value=event_row)
    bot._can_manage_event = AsyncMock(return_value=True)
    bot._sync_event_presentation = AsyncMock()
    # bind the real room+selector resolver so the parsing logic is exercised;
    # it delegates to the mocked _resolve_event_room for the actual room lookup
    bot._resolve_event_and_selector = CommunityBot._resolve_event_and_selector.__get__(bot)
    bot.is_user_in_parent_space = AsyncMock(return_value=True)
    bot.check_parent_room = AsyncMock(return_value=True)
    return bot


# The command handlers are wrapped by maubot's @command decorator and by
# @decorators.require_parent_room; unwrap to the require_parent_room wrapper via
# __mb_func__ so we can invoke them as plain coroutines. check_parent_room is
# stubbed True on the bot so the wrapper passes through.
_REMOVE_LINK = CommunityBot.event_remove_link.__mb_func__
_EDIT_LINK = CommunityBot.event_edit_link.__mb_func__
_LINKS = CommunityBot.event_links.__mb_func__


def _make_msg_evt():
    evt = Mock()
    evt.sender = "@host:example.com"
    evt.room_id = EVENT_ROOM
    evt.reply = AsyncMock()
    evt.respond = AsyncMock()
    return evt


def _stored_links(bot):
    """Extract the JSON links passed to the last database.execute UPDATE."""
    args, _ = bot.database.execute.call_args
    return json.loads(args[1])


class TestEventLinkHandlers:
    @pytest.mark.asyncio
    async def test_remove_link_updates_db_and_syncs(self):
        row = _make_event_row(
            [
                {"label": "First", "url": "https://one.com"},
                {"label": "Second", "url": "https://two.com"},
            ]
        )
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _REMOVE_LINK(bot, evt, f"{EVENT_ROOM} 1")
        bot.database.execute.assert_awaited_once()
        assert _stored_links(bot) == [{"label": "Second", "url": "https://two.com"}]
        bot._sync_event_presentation.assert_awaited_once_with(EVENT_ROOM)
        evt.reply.assert_awaited()

    @pytest.mark.asyncio
    async def test_remove_link_no_match_does_not_update(self):
        row = _make_event_row([{"label": "First", "url": "https://one.com"}])
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _REMOVE_LINK(bot, evt, f"{EVENT_ROOM} nope")
        bot.database.execute.assert_not_awaited()
        bot._sync_event_presentation.assert_not_awaited()
        evt.reply.assert_awaited()

    @pytest.mark.asyncio
    async def test_edit_link_changes_url(self):
        row = _make_event_row([{"label": "First", "url": "https://one.com"}])
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _EDIT_LINK(
            bot, evt, f"{EVENT_ROOM} 1 --url https://new.com --label Renamed"
        )
        bot.database.execute.assert_awaited_once()
        assert _stored_links(bot) == [{"label": "Renamed", "url": "https://new.com"}]
        bot._sync_event_presentation.assert_awaited_once_with(EVENT_ROOM)

    @pytest.mark.asyncio
    async def test_edit_link_requires_new_value(self):
        row = _make_event_row([{"label": "First", "url": "https://one.com"}])
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _EDIT_LINK(bot, evt, f"{EVENT_ROOM} 1")
        bot.database.execute.assert_not_awaited()
        evt.reply.assert_awaited()

    @pytest.mark.asyncio
    async def test_edit_link_no_match(self):
        row = _make_event_row([{"label": "First", "url": "https://one.com"}])
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _EDIT_LINK(
            bot, evt, f"{EVENT_ROOM} nope --url https://x.com"
        )
        bot.database.execute.assert_not_awaited()
        bot._sync_event_presentation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_links_list_responds(self):
        row = _make_event_row([{"label": "First", "url": "https://one.com"}])
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _LINKS(bot, evt, EVENT_ROOM)
        evt.respond.assert_awaited_once()
        args, kwargs = evt.respond.call_args
        assert "First" in args[0]
        assert "Links for Test Event" in args[0]

    @pytest.mark.asyncio
    async def test_links_list_empty(self):
        row = _make_event_row([])
        bot = _make_link_bot(row)
        evt = _make_msg_evt()
        await _LINKS(bot, evt, EVENT_ROOM)
        args, _ = evt.respond.call_args
        assert "No links attached to this event." in args[0]

    @pytest.mark.asyncio
    async def test_remove_link_permission_denied(self):
        row = _make_event_row([{"label": "First", "url": "https://one.com"}])
        bot = _make_link_bot(row)
        bot._can_manage_event = AsyncMock(return_value=False)
        evt = _make_msg_evt()
        await _REMOVE_LINK(bot, evt, f"{EVENT_ROOM} 1")
        bot.database.execute.assert_not_awaited()
        evt.reply.assert_awaited()
