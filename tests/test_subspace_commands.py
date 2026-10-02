"""Tests for the subspace management commands: `space create`, `space list`,
and `room move`.

These exercise the bot methods directly. The bot is created with __new__ to
bypass the heavy constructor (mirrors tests/test_space_roomlist.py), and only
the attributes the methods touch are set. No live Matrix calls are made — the
client is a Mock with AsyncMock methods.
"""

import pytest
from unittest.mock import Mock, AsyncMock, call

from mautrix.types import EventType

from community.bot import CommunityBot


# --- test fixtures / helpers -------------------------------------------------


def _child_event(state_key, via=("example.com",)):
    """Build a mock m.space.child state event pointing at state_key."""
    evt = Mock()
    evt.type = EventType.SPACE_CHILD
    evt.content = Mock()
    evt.content.via = list(via) if via else None
    evt.state_key = state_key
    return evt


def _parent_event(state_key, via=("example.com",)):
    """Build a mock m.space.parent state event pointing at state_key."""
    evt = Mock()
    evt.type = EventType.SPACE_PARENT
    evt.content = Mock()
    evt.content.via = list(via) if via else None
    evt.state_key = state_key
    return evt


def _create_content(is_space_flag):
    content = Mock()
    content.type = "m.space" if is_space_flag else None
    content.get = Mock(return_value="m.space" if is_space_flag else None)
    return content


def _make_evt(sender="@admin:example.com", room_id="!current:example.com"):
    evt = Mock()
    evt.sender = sender
    evt.room_id = room_id
    evt.mark_read = AsyncMock()
    evt.reply = AsyncMock()
    evt.respond = AsyncMock(return_value="msgid")
    return evt


def _make_bot(state_map, space_flags, parent="!parent:example.com"):
    """Create a bare CommunityBot wired to a fake space tree."""
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {"parent_room": parent, "sleep": 0}
    bot.log = Mock()
    bot._roomlist_cache = None

    client = Mock()

    async def fake_get_state(room_id):
        return state_map.get(room_id, [])

    async def fake_get_state_event(room_id, event_type, state_key=""):
        if event_type == EventType.ROOM_CREATE:
            return _create_content(space_flags.get(room_id, False))
        if event_type == EventType.ROOM_NAME:
            name = Mock()
            name.__getitem__ = lambda self, k: room_id
            return {"name": room_id}
        if event_type == EventType.SPACE_CHILD:
            # look through the parent's state for a matching child link
            for st in state_map.get(room_id, []):
                if st.type == EventType.SPACE_CHILD and st.state_key == state_key:
                    return st.content
            from mautrix.errors import MNotFound

            raise MNotFound("m_not_found", "no such child")
        return None

    client.get_state = AsyncMock(side_effect=fake_get_state)
    client.get_state_event = AsyncMock(side_effect=fake_get_state_event)
    client.send_state_event = AsyncMock()
    client.parse_user_id = Mock(return_value=("bot", "example.com"))
    client.mxid = "@bot:example.com"
    bot.client = client
    return bot


# --- space create ------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_create_nests_under_parent_by_default(monkeypatch):
    """With no target, a new subspace links under the configured parent_room."""
    bot = _make_bot({"!parent:example.com": []}, space_flags={})
    bot.create_space = AsyncMock(
        return_value=("!sub:example.com", "#projects:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="projects"
    )

    # create_space called with the given name
    bot.create_space.assert_awaited_once()
    assert bot.create_space.await_args.args[0] == "projects"

    # linked under the parent room
    add_to_space.assert_awaited_once()
    args = add_to_space.await_args.args
    assert args[1] == "!parent:example.com"  # parent_room
    assert args[2] == "!sub:example.com"  # subspace id

    # m.space.parent set in the subspace pointing at the parent
    bot.client.send_state_event.assert_any_call(
        "!sub:example.com",
        EventType.SPACE_PARENT,
        {"via": ["example.com"], "canonical": True},
        state_key="!parent:example.com",
    )

    # cache invalidated after the tree mutation
    bot._invalidate_roomlist_cache.assert_called_once()


@pytest.mark.asyncio
async def test_space_create_nests_under_target_subspace(monkeypatch):
    """With a target subspace id, the new subspace links under that target."""
    state_map = {
        "!parent:example.com": [_child_event("!existing:example.com")],
        "!existing:example.com": [],
    }
    bot = _make_bot(state_map, space_flags={"!existing:example.com": True})
    bot.create_space = AsyncMock(
        return_value=("!new:example.com", "#deep:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="deep --under !existing:example.com"
    )

    add_to_space.assert_awaited_once()
    assert add_to_space.await_args.args[1] == "!existing:example.com"
    assert add_to_space.await_args.args[2] == "!new:example.com"


@pytest.mark.asyncio
async def test_space_create_rejects_non_space_target(monkeypatch):
    """A target that exists but is not a space is rejected before creation."""
    state_map = {
        "!parent:example.com": [_child_event("!room:example.com")],
    }
    bot = _make_bot(state_map, space_flags={})  # !room is not a space
    bot.create_space = AsyncMock()
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="deep --under !room:example.com"
    )

    evt.reply.assert_awaited()
    bot.create_space.assert_not_awaited()
    add_to_space.assert_not_awaited()


@pytest.mark.asyncio
async def test_space_create_multiword_name_under_parent(monkeypatch):
    """A multi-word name (no flag) is passed verbatim to create_space and linked under parent."""
    bot = _make_bot({"!parent:example.com": []}, space_flags={})
    bot.create_space = AsyncMock(
        return_value=("!sub:example.com", "#x:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="wreck's new subspace"
    )

    bot.create_space.assert_awaited_once()
    assert bot.create_space.await_args.args[0] == "wreck's new subspace"

    add_to_space.assert_awaited_once()
    args = add_to_space.await_args.args
    assert args[1] == "!parent:example.com"
    assert args[2] == "!sub:example.com"


@pytest.mark.asyncio
async def test_space_create_sets_restricted_join_rule(monkeypatch):
    """New subspaces are created with a restricted join rule allowing members of
    the top-level parent space to self-join."""
    bot = _make_bot({"!parent:example.com": []}, space_flags={})
    bot.create_space = AsyncMock(
        return_value=("!sub:example.com", "#projects:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="projects"
    )

    bot.create_space.assert_awaited_once()
    initial_state = bot.create_space.await_args.kwargs["initial_state"]
    assert isinstance(initial_state, list) and len(initial_state) == 1
    jr = initial_state[0]
    assert jr["type"] == "m.room.join_rules"
    assert jr["content"]["join_rule"] == "restricted"
    assert jr["content"]["allow"] == [
        {"type": "m.room_membership", "room_id": "!parent:example.com"}
    ]


@pytest.mark.asyncio
async def test_space_create_seeds_power_levels_from_parent(monkeypatch):
    """A new subspace is created with a power_level_override so community admins
    (the parent's user power levels) are admins in the subspace too."""
    bot = _make_bot({"!parent:example.com": []}, space_flags={})
    bot.config["invite_power_level"] = 50
    bot.create_space = AsyncMock(
        return_value=("!sub:example.com", "#projects:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="projects"
    )

    bot.create_space.assert_awaited_once()
    # a power_level_override was supplied (not None) so the subspace is seeded
    assert bot.create_space.await_args.kwargs.get("power_level_override") is not None


@pytest.mark.asyncio
async def test_space_create_multiword_name_with_under_target(monkeypatch):
    """A multi-word name with --under flag nests under the given target."""
    state_map = {
        "!parent:example.com": [_child_event("!existing:example.com")],
        "!existing:example.com": [],
    }
    bot = _make_bot(state_map, space_flags={"!existing:example.com": True})
    bot.create_space = AsyncMock(
        return_value=("!new:example.com", "#deep:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="my cool space --under !existing:example.com"
    )

    bot.create_space.assert_awaited_once()
    assert bot.create_space.await_args.args[0] == "my cool space"

    add_to_space.assert_awaited_once()
    assert add_to_space.await_args.args[1] == "!existing:example.com"
    assert add_to_space.await_args.args[2] == "!new:example.com"


@pytest.mark.asyncio
async def test_space_create_target_flag_synonym(monkeypatch):
    """--target is a synonym for --under and behaves identically."""
    state_map = {
        "!parent:example.com": [_child_event("!existing:example.com")],
        "!existing:example.com": [],
    }
    bot = _make_bot(state_map, space_flags={"!existing:example.com": True})
    bot.create_space = AsyncMock(
        return_value=("!new:example.com", "#deep:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="my cool space --target !existing:example.com"
    )

    bot.create_space.assert_awaited_once()
    assert bot.create_space.await_args.args[0] == "my cool space"

    add_to_space.assert_awaited_once()
    assert add_to_space.await_args.args[1] == "!existing:example.com"
    assert add_to_space.await_args.args[2] == "!new:example.com"


@pytest.mark.asyncio
async def test_space_create_collapses_whitespace(monkeypatch):
    """Interior whitespace in the name is collapsed to single spaces."""
    bot = _make_bot({"!parent:example.com": []}, space_flags={})
    bot.create_space = AsyncMock(
        return_value=("!sub:example.com", "#x:example.com")
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args="my    spaced   name"
    )

    bot.create_space.assert_awaited_once()
    assert bot.create_space.await_args.args[0] == "my spaced name"


@pytest.mark.asyncio
async def test_space_create_empty_shows_usage(monkeypatch):
    """An empty args string shows usage and does not call create_space."""
    bot = _make_bot({"!parent:example.com": []}, space_flags={})
    bot.create_space = AsyncMock()
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )

    evt = _make_evt()
    await CommunityBot.space_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, args=""
    )

    evt.reply.assert_awaited()
    bot.create_space.assert_not_awaited()


# --- room create --under -----------------------------------------------------


def _make_room_create_bot():
    """Bare bot wired for room_create unit tests (create_room etc. mocked)."""
    from unittest.mock import Mock, AsyncMock
    from community.bot import CommunityBot

    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {
        "parent_room": "!parent:example.com",
        "use_community_slug": True,
        "community_slug": "c",
    }
    bot.log = Mock()
    bot.create_room = AsyncMock(return_value=("!new:example.com", "#new-c:example.com"))
    bot.validate_room_aliases = AsyncMock(return_value=(True, []))
    bot._resolve_nesting_target = AsyncMock(return_value="!sub:example.com")
    return bot


@pytest.mark.asyncio
async def test_room_create_without_under_targets_none():
    """A plain room create passes target_parent=None to create_room."""
    bot = _make_room_create_bot()
    evt = _make_evt()
    await CommunityBot.room_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, roomname="cool room"
    )
    bot.create_room.assert_awaited_once()
    assert bot.create_room.await_args.kwargs.get("target_parent") is None
    # the --under flag was absent, so the room name is unchanged
    assert bot.create_room.await_args.args[0] == "cool room"


@pytest.mark.asyncio
async def test_room_create_under_nests_in_subspace():
    """`room create <name> --under <subspace>` resolves the target and passes it."""
    bot = _make_room_create_bot()
    evt = _make_evt()
    await CommunityBot.room_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, roomname="cool room --under Projects"
    )
    bot._resolve_nesting_target.assert_awaited_once()
    assert bot._resolve_nesting_target.await_args.args[0] == "Projects"
    bot.create_room.assert_awaited_once()
    assert bot.create_room.await_args.kwargs.get("target_parent") == "!sub:example.com"
    # the flag was stripped from the room name
    assert bot.create_room.await_args.args[0] == "cool room"


@pytest.mark.asyncio
async def test_room_create_under_invalid_target_aborts():
    """If the target can't be resolved, no room is created."""
    bot = _make_room_create_bot()
    bot._resolve_nesting_target = AsyncMock(return_value=None)  # resolution failed/replied
    evt = _make_evt()
    await CommunityBot.room_create.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, roomname="cool room --under Nonexistent"
    )
    bot.create_room.assert_not_awaited()


# --- room move ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_move_reparents(monkeypatch):
    """Moving a room removes the old links and adds new ones."""
    # tree: parent -> {roomA, subspace}; move roomA into subspace
    state_map = {
        "!parent:example.com": [
            _child_event("!roomA:example.com"),
            _child_event("!sub:example.com"),
        ],
        "!sub:example.com": [],
        # roomA points back at parent
        "!roomA:example.com": [_parent_event("!parent:example.com")],
    }
    bot = _make_bot(state_map, space_flags={"!sub:example.com": True})
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )
    bot._invalidate_roomlist_cache = Mock()

    evt = _make_evt()
    await CommunityBot.room_move.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, room="!roomA:example.com", target_space="!sub:example.com"
    )

    # removed child link from old parent (empty content)
    bot.client.send_state_event.assert_any_call(
        "!parent:example.com",
        EventType.SPACE_CHILD,
        {},
        state_key="!roomA:example.com",
    )
    # removed parent link from the room (empty content)
    bot.client.send_state_event.assert_any_call(
        "!roomA:example.com",
        EventType.SPACE_PARENT,
        {},
        state_key="!parent:example.com",
    )
    # added under the target space (via add_room_to_space)
    add_to_space.assert_awaited_once()
    assert add_to_space.await_args.args[1] == "!sub:example.com"
    assert add_to_space.await_args.args[2] == "!roomA:example.com"
    # new parent link set in the room (populated content)
    bot.client.send_state_event.assert_any_call(
        "!roomA:example.com",
        EventType.SPACE_PARENT,
        {"via": ["example.com"], "canonical": True},
        state_key="!sub:example.com",
    )
    bot._invalidate_roomlist_cache.assert_called_once()


@pytest.mark.asyncio
async def test_room_move_self_move_rejected(monkeypatch):
    """Moving a room into itself is rejected."""
    state_map = {
        "!parent:example.com": [_child_event("!sub:example.com")],
        "!sub:example.com": [],
    }
    bot = _make_bot(state_map, space_flags={"!sub:example.com": True})
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )

    evt = _make_evt()
    await CommunityBot.room_move.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, room="!sub:example.com", target_space="!sub:example.com"
    )

    evt.reply.assert_awaited()
    add_to_space.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_move_cycle_rejected(monkeypatch):
    """Moving a space into one of its own descendants is rejected."""
    # subA contains subB; try to move subA into subB (a descendant) -> cycle
    state_map = {
        "!parent:example.com": [_child_event("!subA:example.com")],
        "!subA:example.com": [_child_event("!subB:example.com")],
        "!subB:example.com": [],
    }
    bot = _make_bot(
        state_map,
        space_flags={"!subA:example.com": True, "!subB:example.com": True},
    )
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )

    evt = _make_evt()
    await CommunityBot.room_move.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, room="!subA:example.com", target_space="!subB:example.com"
    )

    evt.reply.assert_awaited()
    add_to_space.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_move_non_space_target_rejected(monkeypatch):
    """Moving a room into a non-space target is rejected."""
    state_map = {
        "!parent:example.com": [
            _child_event("!roomA:example.com"),
            _child_event("!roomB:example.com"),
        ],
        "!roomA:example.com": [_parent_event("!parent:example.com")],
    }
    bot = _make_bot(state_map, space_flags={})  # neither room is a space
    add_to_space = AsyncMock()
    monkeypatch.setattr(
        "community.helpers.room_creation_utils.add_room_to_space", add_to_space
    )

    evt = _make_evt()
    await CommunityBot.room_move.__mb_func__.__wrapped__.__wrapped__(
        bot, evt, room="!roomA:example.com", target_space="!roomB:example.com"
    )

    evt.reply.assert_awaited()
    add_to_space.assert_not_awaited()
