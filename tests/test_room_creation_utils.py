"""Tests for join-rule helpers in room_creation_utils.

restricted_join_rule_state() is the shared builder used by both managed room
creation and subspace creation so both default to community-membership access.
"""

import pytest

from community.helpers import room_creation_utils


def test_restricted_join_rule_state_shape():
    jr = room_creation_utils.restricted_join_rule_state("!parent:example.com")
    assert jr["type"] == "m.room.join_rules"
    assert jr["content"]["join_rule"] == "restricted"
    assert jr["content"]["allow"] == [
        {"type": "m.room_membership", "room_id": "!parent:example.com"}
    ]


def test_prepare_initial_state_includes_restricted_join_rule():
    """With a parent_room, prepare_initial_state emits the space-parent link and
    the restricted join rule referencing that parent."""
    config = {"encrypt": False}
    state = room_creation_utils.prepare_initial_state(
        config,
        parent_room="!parent:example.com",
        server="example.com",
        force_encryption=False,
        force_unencryption=False,
    )
    join_rules = [e for e in state if e["type"] == "m.room.join_rules"]
    assert len(join_rules) == 1
    jr = join_rules[0]
    assert jr["content"]["join_rule"] == "restricted"
    assert jr["content"]["allow"] == [
        {"type": "m.room_membership", "room_id": "!parent:example.com"}
    ]


def test_prepare_initial_state_no_parent_has_no_join_rule():
    """With no parent_room, no space-parent link or restricted join rule is added."""
    config = {"encrypt": False}
    state = room_creation_utils.prepare_initial_state(
        config,
        parent_room="",
        server="example.com",
        force_encryption=False,
        force_unencryption=False,
    )
    assert [e for e in state if e["type"] == "m.room.join_rules"] == []


def test_extract_target_flag_none():
    text, target = room_creation_utils.extract_target_flag("cool room")
    assert target is None
    assert text == "cool room"


def test_extract_target_flag_under():
    text, target = room_creation_utils.extract_target_flag("cool room --under Projects")
    assert target == "Projects"
    assert text == "cool room"


def test_extract_target_flag_target_synonym():
    text, target = room_creation_utils.extract_target_flag("cool room --target Projects")
    assert target == "Projects"
    assert text == "cool room"


def test_extract_target_flag_multiword_target():
    text, target = room_creation_utils.extract_target_flag("my room --under Sub V12")
    assert target == "Sub V12"
    assert text == "my room"


def test_extract_target_flag_coexists_with_encrypted_after():
    # --encrypted after --under: target stops at the next flag, --encrypted stays in the name
    text, target = room_creation_utils.extract_target_flag(
        "cool room --under Projects --encrypted"
    )
    assert target == "Projects"
    assert text == "cool room --encrypted"


def test_extract_target_flag_coexists_with_encrypted_before():
    # --encrypted before --under: target runs to end, --encrypted stays in the name
    text, target = room_creation_utils.extract_target_flag(
        "cool room --encrypted --under Projects"
    )
    assert target == "Projects"
    assert text == "cool room --encrypted"


def test_extract_target_flag_alias_target():
    text, target = room_creation_utils.extract_target_flag(
        "cool room --under #projects-c:example.com"
    )
    assert target == "#projects-c:example.com"
    assert text == "cool room"


def test_prepare_initial_state_join_rule_room_decoupled():
    """When join_rule_room is given, the space-parent links to parent_room but the
    restricted join rule gates on join_rule_room (used for rooms nested under a
    subspace that should still be community-joinable)."""
    state = room_creation_utils.prepare_initial_state(
        {"encrypt": False},
        parent_room="!subspace:example.com",
        server="example.com",
        force_encryption=False,
        force_unencryption=False,
        join_rule_room="!community:example.com",
    )
    space_parent = [e for e in state if e["type"] == "m.space.parent"]
    assert len(space_parent) == 1
    assert space_parent[0]["state_key"] == "!subspace:example.com"
    join_rules = [e for e in state if e["type"] == "m.room.join_rules"]
    assert len(join_rules) == 1
    assert join_rules[0]["content"]["allow"] == [
        {"type": "m.room_membership", "room_id": "!community:example.com"}
    ]


def test_merge_user_power_levels_takes_higher():
    base = {"@a:x": 100, "@b:x": 50}
    extra = {"@b:x": 100, "@c:x": 50}
    merged = room_creation_utils.merge_user_power_levels(base, extra)
    assert merged == {"@a:x": 100, "@b:x": 100, "@c:x": 50}


def test_merge_user_power_levels_does_not_lower():
    base = {"@a:x": 100}
    extra = {"@a:x": 50}
    merged = room_creation_utils.merge_user_power_levels(base, extra)
    assert merged["@a:x"] == 100


def test_merge_user_power_levels_handles_none():
    assert room_creation_utils.merge_user_power_levels(None, {"@a:x": 50}) == {"@a:x": 50}
    assert room_creation_utils.merge_user_power_levels({"@a:x": 50}, None) == {"@a:x": 50}
    assert room_creation_utils.merge_user_power_levels(None, None) == {}


def test_merge_user_power_levels_does_not_mutate_inputs():
    base = {"@a:x": 100}
    extra = {"@b:x": 50}
    room_creation_utils.merge_user_power_levels(base, extra)
    assert base == {"@a:x": 100}
    assert extra == {"@b:x": 50}


def test_pick_placement_parent_prefers_subspace():
    # old room lived in a subspace -> keep it there, not the top-level parent
    assert (
        room_creation_utils.pick_placement_parent(
            ["!sub:x"], "!parent:x"
        )
        == "!sub:x"
    )


def test_pick_placement_parent_prefers_subspace_over_parent():
    # if both the subspace and the top-level parent are listed, prefer the subspace
    assert (
        room_creation_utils.pick_placement_parent(
            ["!parent:x", "!sub:x"], "!parent:x"
        )
        == "!sub:x"
    )


def test_pick_placement_parent_top_level_room():
    # old room was a direct child of the top-level parent
    assert (
        room_creation_utils.pick_placement_parent(["!parent:x"], "!parent:x")
        == "!parent:x"
    )


def test_pick_placement_parent_no_parents_falls_back():
    assert room_creation_utils.pick_placement_parent([], "!parent:x") == "!parent:x"
    assert room_creation_utils.pick_placement_parent(None, "!parent:x") == "!parent:x"
