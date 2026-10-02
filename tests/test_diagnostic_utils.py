"""Tests for doctor diagnostic summaries, focused on v12 creator-awareness:
a creator with unlimited power must not be reported as a permission problem just
because other users hold numeric power levels."""

from community.helpers import diagnostic_utils


def _is_modern(v):
    return str(v) == "12"


def test_room_summary_skips_creator_room_with_power_conflicts():
    rooms = {
        "!creator:x": {
            "has_admin": True,
            "bot_has_unlimited_power": True,
            "users_higher": [{"user": "@a:x", "level": 100}],
            "room_name": "Creator Room",
            "room_version": "12",
            "bot_power_level": 0,
        }
    }
    summary, stats = diagnostic_utils.generate_room_summary(rooms, _is_modern)
    # creator room with high-power users is NOT problematic
    assert "Problematic Rooms" not in summary
    assert stats["admin_rooms"] == 1


def test_room_summary_lists_legacy_room_with_higher_users():
    rooms = {
        "!legacy:x": {
            "has_admin": True,
            "bot_has_unlimited_power": False,
            "users_higher": [{"user": "@a:x", "level": 100}],
            "room_name": "Legacy Room",
            "room_version": "10",
            "bot_power_level": 50,
        }
    }
    summary, stats = diagnostic_utils.generate_room_summary(rooms, _is_modern)
    # non-creator admin room with a higher-power user IS still surfaced
    assert "Problematic Rooms" in summary
    assert "Legacy Room" in summary


def test_room_summary_lists_room_without_admin():
    rooms = {
        "!noadmin:x": {
            "has_admin": False,
            "bot_has_unlimited_power": False,
            "room_name": "No Admin Room",
            "room_version": "10",
            "bot_power_level": 0,
        }
    }
    summary, stats = diagnostic_utils.generate_room_summary(rooms, _is_modern)
    assert "Problematic Rooms" in summary
    assert "No Admin Room" in summary
    assert stats["non_admin_rooms"] == 1


def test_space_summary_creator_hides_higher_power_users():
    space = {
        "has_admin": True,
        "bot_has_unlimited_power": True,
        "bot_power_level": 0,
        "users_higher": [{"user": "@a:x", "level": 100}],
    }
    out = diagnostic_utils.generate_space_summary(space)
    assert "Users with higher power" not in out
    assert "unlimited power - creator" in out


def test_space_summary_non_creator_shows_higher_power_users():
    space = {
        "has_admin": False,
        "bot_has_unlimited_power": False,
        "bot_power_level": 50,
        "users_higher": [{"user": "@a:x", "level": 100}],
    }
    out = diagnostic_utils.generate_space_summary(space)
    assert "Users with higher power" in out
