"""Tests for recursive space room-list traversal and its cache.

These exercise CommunityBot.get_space_roomlist directly. The bot is created
with __new__ to bypass the heavy constructor (see the known pre-existing test
infra issue with instantiating CommunityBot), and only the attributes the
method actually touches are set.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from mautrix.types import EventType

from community.bot import CommunityBot


def _child_event(state_key, via=("example.com",)):
    """Build a mock m.space.child state event pointing at state_key."""
    evt = Mock()
    evt.type = EventType.SPACE_CHILD
    evt.content = Mock()
    evt.content.via = list(via) if via else None
    evt.state_key = state_key
    return evt


def _create_content(is_space_flag):
    """Build a mock m.room.create content object."""
    content = Mock()
    content.type = "m.space" if is_space_flag else None
    content.get = Mock(return_value="m.space" if is_space_flag else None)
    return content


def _make_bot(state_map, space_flags, parent="!parent:example.com"):
    """Create a bare CommunityBot wired to the given fake space tree.

    Args:
        state_map: dict of space_id -> list of child state events
        space_flags: dict of room_id -> bool (whether it is a space)
        parent: configured parent_room
    """
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {"parent_room": parent}
    bot.log = Mock()
    bot._roomlist_cache = None

    client = Mock()

    async def fake_get_state(room_id):
        return state_map.get(room_id, [])

    async def fake_get_state_event(room_id, event_type):
        # is_space only ever asks for the create event
        assert event_type == EventType.ROOM_CREATE
        return _create_content(space_flags.get(room_id, False))

    client.get_state = AsyncMock(side_effect=fake_get_state)
    client.get_state_event = AsyncMock(side_effect=fake_get_state_event)
    bot.client = client
    return bot


@pytest.mark.asyncio
async def test_flat_space_lists_direct_children():
    """A space with no subspaces returns just its direct child rooms."""
    state_map = {
        "!parent:example.com": [_child_event("!r1:x"), _child_event("!r2:x")],
    }
    bot = _make_bot(state_map, space_flags={})
    result = await bot.get_space_roomlist()
    assert result == ["!r1:x", "!r2:x"]


@pytest.mark.asyncio
async def test_recurses_into_subspace():
    """Nested subspace rooms are flattened in, and the subspace itself stays."""
    state_map = {
        "!parent:example.com": [_child_event("!r1:x"), _child_event("!sub:x")],
        "!sub:x": [_child_event("!r2:x"), _child_event("!r3:x")],
    }
    bot = _make_bot(state_map, space_flags={"!sub:x": True})
    result = await bot.get_space_roomlist()
    # subspace room "!sub:x" is included (managed), plus its nested rooms
    assert result == ["!r1:x", "!sub:x", "!r2:x", "!r3:x"]


@pytest.mark.asyncio
async def test_arbitrary_depth():
    """Traversal descends through multiple levels of nesting."""
    state_map = {
        "!parent:example.com": [_child_event("!subA:x")],
        "!subA:x": [_child_event("!subB:x")],
        "!subB:x": [_child_event("!leaf:x")],
    }
    bot = _make_bot(
        state_map, space_flags={"!subA:x": True, "!subB:x": True}
    )
    result = await bot.get_space_roomlist()
    assert result == ["!subA:x", "!subB:x", "!leaf:x"]


@pytest.mark.asyncio
async def test_cycle_protection():
    """A cycle (sub references parent back) terminates without duplication."""
    state_map = {
        "!parent:example.com": [_child_event("!sub:x")],
        # sub points back at parent, forming a cycle
        "!sub:x": [_child_event("!r1:x"), _child_event("!parent:example.com")],
    }
    bot = _make_bot(
        state_map,
        space_flags={"!sub:x": True, "!parent:example.com": True},
    )
    result = await bot.get_space_roomlist()
    # parent is appended as a child link but not re-traversed (already visited)
    assert result == ["!sub:x", "!r1:x", "!parent:example.com"]


@pytest.mark.asyncio
async def test_dedupes_shared_room():
    """A room linked from two subspaces appears only once."""
    state_map = {
        "!parent:example.com": [
            _child_event("!subA:x"),
            _child_event("!subB:x"),
        ],
        "!subA:x": [_child_event("!shared:x")],
        "!subB:x": [_child_event("!shared:x")],
    }
    bot = _make_bot(
        state_map, space_flags={"!subA:x": True, "!subB:x": True}
    )
    result = await bot.get_space_roomlist()
    # depth-first: subA then its child "shared", then subB (shared deduped out)
    assert result == ["!subA:x", "!shared:x", "!subB:x"]
    assert result.count("!shared:x") == 1


@pytest.mark.asyncio
async def test_ignores_children_without_via():
    """Space child links lacking a via path are not real members."""
    state_map = {
        "!parent:example.com": [
            _child_event("!real:x"),
            _child_event("!ghost:x", via=None),
        ],
    }
    bot = _make_bot(state_map, space_flags={})
    result = await bot.get_space_roomlist()
    assert result == ["!real:x"]


@pytest.mark.asyncio
async def test_no_parent_room_returns_empty():
    """With no parent_room configured, the list is empty."""
    bot = _make_bot({}, space_flags={}, parent="")
    result = await bot.get_space_roomlist()
    assert result == []


@pytest.mark.asyncio
async def test_cache_is_reused():
    """The top-level result is cached; a second call skips state fetches."""
    state_map = {
        "!parent:example.com": [_child_event("!r1:x")],
    }
    bot = _make_bot(state_map, space_flags={})

    first = await bot.get_space_roomlist()
    calls_after_first = bot.client.get_state.call_count
    second = await bot.get_space_roomlist()

    assert first == second == ["!r1:x"]
    # No additional state fetches on the cached call
    assert bot.client.get_state.call_count == calls_after_first


@pytest.mark.asyncio
async def test_cache_returns_copy():
    """Callers mutating the returned list must not corrupt the cache."""
    state_map = {
        "!parent:example.com": [_child_event("!r1:x")],
    }
    bot = _make_bot(state_map, space_flags={})

    first = await bot.get_space_roomlist()
    first.append("!parent:example.com")  # callers do this to include the space
    second = await bot.get_space_roomlist()

    assert second == ["!r1:x"]


@pytest.mark.asyncio
async def test_invalidate_forces_rebuild():
    """After invalidation, the next call re-fetches state."""
    state_map = {
        "!parent:example.com": [_child_event("!r1:x")],
    }
    bot = _make_bot(state_map, space_flags={})

    await bot.get_space_roomlist()
    calls_after_first = bot.client.get_state.call_count
    bot._invalidate_roomlist_cache()
    await bot.get_space_roomlist()

    assert bot.client.get_state.call_count > calls_after_first
