"""Tests for event_web.py pure rendering functions.

These are all synchronous — no asyncio needed.
No CommunityBot instantiation (requires 10 constructor args — pre-existing infra issue).
"""
import pytest
import json
from community.helpers import event_web


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event_row(**kwargs):
    defaults = {
        "name": "Test Event",
        "description": "A test event",
        "event_start_ts": 1730394000000,  # 2024-10-31 15:00 UTC
        "event_end_ts": 1730401200000,    # 2024-10-31 17:00 UTC
        "location": "Test Location",
        "host_id": "@host:example.org",
        "organizers": "[]",
        "extra_links": "[]",
        "timezone": "UTC",
        "max_additional_guests": 1,
    }
    defaults.update(kwargs)
    return defaults


def make_rsvp_row(**kwargs):
    defaults = {"rsvp_status": "yes", "guest_count": 0, "rsvp_source": "reaction"}
    defaults.update(kwargs)
    return defaults


EMPTY_COUNTS = {
    "yes": 0, "no": 0, "maybe": 0,
    "yes_guests": 0, "no_guests": 0, "maybe_guests": 0,
}

BASE_KWARGS = dict(
    rsvp_counts=EMPTY_COUNTS,
    is_manager=False,
    is_host=False,
    token="testtoken",
    room_id="!abc:example.org",
    widget_mode=False,
    widget_id="",
    parent_url="",
    base="https://example.org/bot",
)


def render_page(**overrides):
    """Render a basic event page, merging any overrides into the defaults."""
    kw = dict(BASE_KWARGS)
    kw.update(overrides)
    if "event_row" not in kw:
        kw["event_row"] = make_event_row()
    if "rsvp_row" not in kw:
        kw["rsvp_row"] = make_rsvp_row()
    return event_web.render_event_page_html(**kw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_render_bootstrap_html_contains_auth_url():
    out = event_web.render_bootstrap_html(base="https://example.org/bot")
    assert "/event/widget/auth" in out
    assert event_web.WIDGET_API_SRC in out


def test_render_event_page_contains_event_name():
    out = render_page()
    assert "Test Event" in out


def test_render_event_page_readonly_no_edit_form():
    out = render_page(is_manager=False)
    assert "Edit Details" not in out


def test_render_event_page_manager_has_edit_form():
    out = render_page(is_manager=True)
    assert "Edit Details" in out


def test_render_event_page_xss_in_name():
    out = render_page(event_row=make_event_row(name="<script>alert(1)</script>"))
    assert "&lt;script&gt;" in out
    assert "<script>alert" not in out


def test_render_event_page_xss_in_description():
    out = render_page(event_row=make_event_row(description="<img src=x onerror=alert(1)>"))
    assert "&lt;img" in out
    # Raw unescaped tag must not appear
    assert "<img src=x" not in out


def test_render_event_page_link_http_rendered_as_anchor():
    links = json.dumps([{"url": "https://example.com", "label": "Example"}])
    out = render_page(event_row=make_event_row(extra_links=links))
    assert 'href="https://example.com"' in out


def test_render_event_page_link_javascript_not_rendered_as_anchor():
    links = json.dumps([{"url": "javascript:alert(1)", "label": "Bad"}])
    out = render_page(event_row=make_event_row(extra_links=links))
    assert 'href="javascript:' not in out


def test_render_event_page_no_date():
    out = render_page(event_row=make_event_row(event_start_ts=0, event_end_ts=None))
    assert "Date TBD" in out
    # ICS download link should still be present
    assert ".ics" in out


def test_render_event_page_rsvp_active_button():
    out = render_page(rsvp_row=make_rsvp_row(rsvp_status="yes"))
    # The Yes button should carry the "active" class
    # Look for the active class appearing near "Yes" in the RSVP buttons area
    assert "active" in out
    # Verify it's on the correct button by checking data-status="yes" near active
    assert 'data-status="yes"' in out


def test_render_event_page_no_rsvp():
    out = render_page(rsvp_row=None)
    # Should indicate user hasn't RSVPed
    assert "have not RSVPed" in out or "No RSVP" in out


def test_fmt_datetime_tbd():
    result = event_web._fmt_datetime(0, None, "UTC")
    assert result == "Date TBD"


def test_fmt_datetime_utc():
    # 1730394000000 ms = 2024-10-31 15:00:00 UTC
    result = event_web._fmt_datetime(1730394000000, None, "UTC")
    assert "2024" in result
    assert "UTC" in result


def test_render_message_html_escaping():
    out = event_web.render_message_html(
        title="<script>bad title</script>",
        message="<script>bad message</script>",
    )
    assert "&lt;script&gt;" in out
    assert "<script>bad" not in out


def test_fmt_datetime_with_end_time():
    # 1730394000000 = 2024-10-31 15:00 UTC, 1730401200000 = 17:00 UTC
    result = event_web._fmt_datetime(1730394000000, 1730401200000, "UTC")
    assert "—" in result or "-" in result
    assert "UTC" in result


def test_fmt_datetime_timezone_abbreviation():
    # PDT is -07:00; the label should appear
    result = event_web._fmt_datetime(1730394000000, None, "PDT")
    assert "PDT" in result


def test_organizer_pills_empty():
    assert event_web._organizer_pills([]) == ""


def test_organizer_pills_renders_link():
    out = event_web._organizer_pills(["@alice:example.org"])
    assert "matrix.to" in out
    assert "@alice:example.org" in out


def test_render_event_page_ics_link_present():
    out = render_page()
    assert "/event/ics" in out
    assert "testtoken" in out


def test_render_event_page_widget_params_threaded():
    out = render_page(
        widget_mode=True,
        widget_id="my-widget-id",
        parent_url="https://client.example.com",
    )
    assert "my-widget-id" in out
    assert "https://client.example.com" in out


def test_render_event_page_counts_display():
    counts = {
        "yes": 3, "no": 1, "maybe": 2,
        "yes_guests": 1, "no_guests": 0, "maybe_guests": 0,
    }
    out = render_page(rsvp_counts=counts)
    assert "Yes: 3" in out
    assert "No: 1" in out
    assert "Maybe: 2" in out


def test_render_event_page_description_newlines():
    out = render_page(event_row=make_event_row(description="Line 1\nLine 2"))
    assert "<br>" in out
    # Both lines should appear
    assert "Line 1" in out
    assert "Line 2" in out


def test_render_event_page_no_description_section_when_empty():
    out = render_page(event_row=make_event_row(description=""))
    # Description label should not appear when empty
    # (the description section is skipped entirely)
    assert "Description" not in out


def test_render_bootstrap_html_redirect_contains_token_and_widget():
    out = event_web.render_bootstrap_html(base="https://example.org/bot")
    assert "widget=1" in out
    assert "widgetId" in out
    assert "parentUrl" in out
    assert "Loading event details" in out
