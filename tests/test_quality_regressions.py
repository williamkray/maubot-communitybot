from community.bot import CommunityBot


def test_matrix_uri_wrappers_delegate_to_canonical_helpers() -> None:
    bot = CommunityBot.__new__(CommunityBot)
    bot.config = {}
    assert bot._matrix_user_uri("@alice:example.org") == "matrix:u/alice:example.org?action=chat"
    assert bot._matrix_room_uri("!roomid:example.org") == "matrix:roomid/roomid:example.org"
    assert bot._matrix_room_uri("!roomid:example.org", "#general:example.org") == "matrix:r/general:example.org"
    assert bot._matrix_event_uri("!roomid:example.org", "$eventid") == "matrix:roomid/roomid:example.org/e/eventid"
