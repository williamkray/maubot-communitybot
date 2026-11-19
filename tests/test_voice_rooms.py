"""Voice (MatrixRTC call) room creation and call power levels.

Voice rooms use the MSC3417 m.room.create type, let any member start/join a
call, and pre-open an MSC4143 call slot. Text rooms gate calls on
``call_power_level``. Every room restates the server's default per-event
levels, since power_level_content_override replaces the whole events map.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from mautrix.types import PowerLevelStateEventContent

from community.bot import CommunityBot
from community.helpers import room_creation_utils as rcu

SERVER = "example.com"
PARENT = "!parent:example.com"


# --- helpers ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hangout --voice", ("hangout", True)),
        ("--voice hangout", ("hangout", True)),
        ("cool --voice room", ("cool room", True)),
        ("-voice hangout", ("hangout", True)),
        ("hangout", ("hangout", False)),
        ("voiceover", ("voiceover", False)),
        ("hangout --voices", ("hangout --voices", False)),
        ("", ("", False)),
    ],
)
def test_extract_voice_flag(text, expected):
    assert rcu.extract_voice_flag(text) == expected


def test_voice_flag_coexists_with_other_flags():
    text, voice = rcu.extract_voice_flag("hangout --voice --encrypted --under Projects")
    assert voice
    assert rcu.extract_encryption_flags(text) == ("hangout --under Projects", True, False)


def test_is_call_room_type():
    from mautrix.types import RoomType

    assert rcu.is_call_room_type("org.matrix.msc3417.call")
    assert rcu.is_call_room_type(RoomType("org.matrix.msc3417.call"))
    assert not rcu.is_call_room_type(None)
    assert not rcu.is_call_room_type("m.space")


def test_event_levels_restate_server_defaults():
    for voice in (True, False):
        events = rcu.call_event_power_levels(voice)
        for event_type, level in rcu.DEFAULT_EVENT_POWER_LEVELS.items():
            assert events[event_type] == level


def test_voice_room_event_levels_let_members_join():
    events = rcu.call_event_power_levels(voice=True, call_power_level=100)
    assert events["org.matrix.msc3401.call.member"] == 0
    assert events["org.matrix.msc4143.rtc.member"] == 0
    assert events["m.rtc.member"] == 0
    # the slot is pre-opened; only closing it needs moderator power, and
    # call_power_level does not apply to voice rooms
    assert events["org.matrix.msc4143.rtc.slot"] == 50
    assert events["m.rtc.slot"] == 50


def test_text_room_event_levels_follow_call_power_level():
    events = rcu.call_event_power_levels(voice=False, call_power_level=75)
    assert events["org.matrix.msc3401.call.member"] == 75
    assert events["org.matrix.msc3401.call"] == 75
    assert events["org.matrix.msc4143.rtc.slot"] == 75
    assert events["m.rtc.slot"] == 75
    # once a moderator opens a Matrix 2.0 slot, members may join
    assert events["org.matrix.msc4143.rtc.member"] == 0


def test_voice_room_creation_content_preserves_existing():
    pred = {"predecessor": {"room_id": "!old:example.com", "event_id": "$e"}}
    content = rcu.voice_room_creation_content(pred)
    assert content == {**pred, "type": "org.matrix.msc3417.call"}
    assert "type" not in pred  # input not mutated
    assert rcu.voice_room_creation_content(None) == {"type": "org.matrix.msc3417.call"}


@pytest.mark.parametrize("encrypted", [True, False])
def test_voice_room_initial_state(encrypted):
    state = rcu.voice_room_initial_state(encrypted)
    call = next(e for e in state if e["type"] == "org.matrix.msc3401.call")
    assert call == {"type": "org.matrix.msc3401.call", "state_key": "", "content": {}}
    # unstable slot for deployed clients, stable slot for Matrix 2.0 clients
    # (e.g. Sable on an org.matrix.msc4143.stable homeserver)
    for slot_type, slot_key, enc_type in [
        ("org.matrix.msc4143.rtc.slot", "m.call#ROOM", "org.matrix.msc4143.per_member"),
        ("m.rtc.slot", "m.call#room", "m.per_member"),
    ]:
        slot = next(e for e in state if e["type"] == slot_type)
        assert slot["state_key"] == slot_key
        assert slot["content"]["status"] == "open"
        assert slot["content"]["application"] == {"type": "m.call"}
        if encrypted:
            assert slot["content"]["encryption"] == {"type": enc_type}
        else:
            assert "encryption" not in slot["content"]


# --- create_room wiring ------------------------------------------------------


def make_bot(**cfg):
    bot = Mock(spec=CommunityBot)
    bot.log = Mock()
    bot.client = Mock()
    bot.client.mxid = "@bot:example.com"
    bot.client.parse_user_id = Mock(return_value=("bot", SERVER))
    bot.client.create_room = AsyncMock(return_value="!new:example.com")
    parent_pl = PowerLevelStateEventContent()
    parent_pl.users = {"@admin:example.com": 100}
    bot.client.get_state_event = AsyncMock(return_value=parent_pl)
    bot.validate_room_alias = AsyncMock(return_value=True)
    bot.is_modern_room_version = Mock(return_value=False)
    bot.config = {
        "invitees": [],
        "room_version": "10",
        "use_community_slug": False,
        "parent_room": PARENT,
        "sleep": 0,
    }
    bot.config.update(cfg)
    return bot


async def _create(bot, *args, **kwargs):
    with patch.object(rcu, "verify_room_creation", AsyncMock()), patch.object(
        rcu, "add_room_to_space", AsyncMock()
    ):
        result = await CommunityBot.create_room(bot, *args, **kwargs)
    assert result == ("!new:example.com", "#hangout:example.com")
    return bot.client.create_room.await_args.kwargs


@pytest.mark.asyncio
async def test_create_voice_room_payload():
    bot = make_bot(encrypt=True)
    kwargs = await _create(bot, "hangout", None, voice=True)

    assert kwargs["creation_content"]["type"] == "org.matrix.msc3417.call"
    events = kwargs["power_level_override"].events
    assert events["org.matrix.msc3401.call.member"] == 0
    assert events["m.room.power_levels"] == 100
    # parent user levels still flow through
    assert kwargs["power_level_override"].users["@admin:example.com"] == 100

    slot = next(
        e for e in kwargs["initial_state"] if e["type"] == "org.matrix.msc4143.rtc.slot"
    )
    assert slot["content"]["encryption"] == {"type": "org.matrix.msc4143.per_member"}
    stable = next(e for e in kwargs["initial_state"] if e["type"] == "m.rtc.slot")
    assert stable["content"]["encryption"] == {"type": "m.per_member"}
    # the regular join rule / parent link are still there
    types = [e["type"] for e in kwargs["initial_state"]]
    assert "m.room.join_rules" in types
    assert "m.space.parent" in types


@pytest.mark.asyncio
async def test_create_voice_room_unencrypted_slot():
    bot = make_bot(encrypt=False)
    kwargs = await _create(bot, "hangout", None, voice=True)
    slot = next(
        e for e in kwargs["initial_state"] if e["type"] == "org.matrix.msc4143.rtc.slot"
    )
    assert "encryption" not in slot["content"]


@pytest.mark.asyncio
async def test_create_voice_room_keeps_predecessor():
    bot = make_bot()
    pred = {"predecessor": {"room_id": "!old:example.com", "event_id": "$e"}}
    kwargs = await _create(bot, "hangout", None, creation_content=pred, voice=True)
    assert kwargs["creation_content"] == {**pred, "type": "org.matrix.msc3417.call"}


@pytest.mark.asyncio
async def test_create_text_room_gates_calls_on_config():
    bot = make_bot(call_power_level=100)
    kwargs = await _create(bot, "hangout", None)
    assert not (kwargs["creation_content"] or {}).get("type")
    events = kwargs["power_level_override"].events
    assert events["org.matrix.msc3401.call.member"] == 100
    assert events["org.matrix.msc4143.rtc.slot"] == 100
    assert events["m.room.tombstone"] == 100
    types = [e["type"] for e in kwargs["initial_state"]]
    assert "org.matrix.msc4143.rtc.slot" not in types
    assert "org.matrix.msc3401.call" not in types


@pytest.mark.asyncio
async def test_create_text_room_call_power_level_defaults_to_moderator():
    bot = make_bot()
    kwargs = await _create(bot, "hangout", None)
    assert kwargs["power_level_override"].events["org.matrix.msc3401.call.member"] == 50


@pytest.mark.asyncio
async def test_create_voice_room_v12_omits_bot_but_keeps_events():
    bot = make_bot(room_version="12")
    bot.is_modern_room_version = Mock(return_value=True)
    kwargs = await _create(bot, "hangout", None, voice=True)
    pl = kwargs["power_level_override"]
    assert "@bot:example.com" not in pl.users
    assert pl.events["org.matrix.msc3401.call.member"] == 0


@pytest.mark.asyncio
async def test_caller_override_event_levels_win_without_duplicates():
    from mautrix.types import EventType

    bot = make_bot()
    override = PowerLevelStateEventContent.deserialize(
        {"users": {}, "events": {"org.matrix.msc3401.call.member": 25}}
    )
    assert isinstance(next(iter(override.events)), EventType)
    kwargs = await _create(bot, "hangout", None, power_level_override=override)
    events = kwargs["power_level_override"].events
    assert events["org.matrix.msc3401.call.member"] == 25
    assert len(events) == len(set(map(str, events)))
