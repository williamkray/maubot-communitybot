import html
import json
from datetime import datetime, timezone, timedelta

# matrix-widget-api UMD build, version-pinned.
WIDGET_API_SRC = "https://unpkg.com/matrix-widget-api@1.20.0/dist/api.min.js"

# Offsets for display-only timezone abbreviations (timestamps are always UTC).
TZ_DISPLAY_ABBREV = {
    "PST": "-08:00", "PDT": "-07:00", "MST": "-07:00", "MDT": "-06:00",
    "CST": "-06:00", "CDT": "-05:00", "EST": "-05:00", "EDT": "-04:00",
    "UTC": "+00:00", "GMT": "+00:00",
}

# Abbreviation → fixed UTC offset (hours) for pre-filling edit form times when
# ZoneInfo is unavailable. Matches event_utils.TZ_ABBREV_OFFSETS.
_ABBREV_OFFSETS = {
    "PST": -8, "PDT": -7, "MST": -7, "MDT": -6,
    "CST": -6, "CDT": -5, "EST": -5, "EDT": -4,
    "AKST": -9, "AKDT": -8, "HST": -10,
    "GMT": 0, "UTC": 0, "BST": 1, "CET": 1, "CEST": 2,
    "AEST": 10, "AEDT": 11,
}

# Curated list of common IANA timezone names for the autocomplete datalist.
# Grouped roughly by region; presented alphabetically to the browser.
COMMON_TIMEZONES = [
    # Americas
    "America/Anchorage", "America/Chicago", "America/Denver",
    "America/Halifax", "America/Los_Angeles", "America/New_York",
    "America/Phoenix", "America/Regina", "America/Toronto",
    "America/Vancouver", "America/Winnipeg",
    "America/Bogota", "America/Lima", "America/Mexico_City",
    "America/Sao_Paulo", "America/Santiago", "America/Buenos_Aires",
    "Pacific/Honolulu",
    # Europe
    "Europe/Amsterdam", "Europe/Athens", "Europe/Berlin",
    "Europe/Brussels", "Europe/Dublin", "Europe/Helsinki",
    "Europe/Istanbul", "Europe/Lisbon", "Europe/London",
    "Europe/Madrid", "Europe/Moscow", "Europe/Paris",
    "Europe/Rome", "Europe/Stockholm", "Europe/Warsaw",
    # Asia / Pacific
    "Asia/Bangkok", "Asia/Colombo", "Asia/Dubai",
    "Asia/Hong_Kong", "Asia/Jakarta", "Asia/Karachi",
    "Asia/Kolkata", "Asia/Kuala_Lumpur", "Asia/Seoul",
    "Asia/Shanghai", "Asia/Singapore", "Asia/Taipei",
    "Asia/Tehran", "Asia/Tokyo",
    "Australia/Adelaide", "Australia/Brisbane",
    "Australia/Melbourne", "Australia/Perth", "Australia/Sydney",
    "Pacific/Auckland", "Pacific/Fiji",
    # Africa / Middle East
    "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos",
    "Africa/Nairobi",
    # UTC
    "UTC",
]


def render_style_css() -> str:
    """Return a complete CSS stylesheet for the event widget."""
    return """\
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: #1a1a2e;
  color: #e0e0e0;
  line-height: 1.5;
  min-height: 100dvh;
}

a { color: #7aa2f7; text-decoration: none; }
a:hover { text-decoration: underline; }

/* sticky header */
.ew-header {
  position: sticky;
  top: 0;
  z-index: 100;
  background: #16213e;
  border-bottom: 1px solid #2d3561;
  padding: 10px 16px;
}

.ew-header h1 {
  font-size: 1.1rem;
  font-weight: 600;
  color: #e0e0e0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* content sections */
.ew-section {
  padding: 14px 16px;
  border-bottom: 1px solid #1e2a50;
}

/* label (muted) */
.ew-label {
  font-size: 0.78rem;
  color: #606880;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}

/* RSVP buttons */
.ew-rsvp-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.ew-btn {
  border: 2px solid transparent;
  border-radius: 6px;
  color: #fff;
  cursor: pointer;
  font: inherit;
  font-size: 0.88rem;
  font-weight: 500;
  padding: 6px 14px;
  transition: opacity 0.15s, border-color 0.15s;
}

.ew-btn:hover { opacity: 0.85; }

.ew-btn-yes    { background: #2e7d32; }
.ew-btn-no     { background: #c62828; }
.ew-btn-maybe  { background: #e65100; }
.ew-btn-remove { background: #424242; }
.ew-btn-primary { background: #3d5afe; }
.ew-btn-danger  { background: #7b1c1c; }

.ew-btn.active {
  border-color: #fff;
  box-shadow: 0 0 0 1px rgba(255,255,255,0.4);
}

/* current RSVP status */
.ew-status {
  font-size: 0.92rem;
  color: #a0b4c8;
  margin-bottom: 6px;
}

/* RSVP counts */
.ew-counts {
  font-size: 0.85rem;
  color: #a0aec0;
  margin-top: 8px;
}

/* links list */
.ew-links {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ew-links li { font-size: 0.92rem; }

/* edit form */
.ew-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 10px;
}

.ew-form label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 0.85rem;
  color: #a0aec0;
}

.ew-form input,
.ew-form textarea,
.ew-form select {
  width: 100%;
  background: #111827;
  border: 1px solid #2d3561;
  border-radius: 6px;
  color: #e0e0e0;
  font: inherit;
  font-size: 0.9rem;
  padding: 6px 10px;
  outline: none;
}

.ew-form input:focus,
.ew-form textarea:focus,
.ew-form select:focus {
  border-color: #7aa2f7;
}

.ew-form textarea { min-height: 80px; resize: vertical; }

/* collapsible panels */
.ew-collapsible {
  display: none;
  margin-top: 10px;
}

.ew-collapsible.open { display: block; }

/* cancel confirmation box */
.ew-confirm-box {
  margin-top: 10px;
  padding: 12px;
  background: #2d1515;
  border: 1px solid #7b1c1c;
  border-radius: 6px;
  display: none;
}

.ew-confirm-box.open { display: block; }
.ew-confirm-box p { font-size: 0.9rem; margin-bottom: 8px; }

/* inline status messages */
.ew-msg {
  font-size: 0.85rem;
  margin-top: 6px;
  min-height: 1.2em;
}

.ew-msg.ok  { color: #66bb6a; }
.ew-msg.err { color: #ef5350; }

/* guest count selector inline */
.ew-guest-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 0.88rem;
  color: #a0aec0;
}

.ew-guest-row select {
  background: #111827;
  border: 1px solid #2d3561;
  border-radius: 6px;
  color: #e0e0e0;
  font: inherit;
  font-size: 0.88rem;
  padding: 4px 8px;
}

/* organizer pills */
.ew-pill {
  display: inline-block;
  background: #1e2a50;
  border: 1px solid #2d3561;
  border-radius: 20px;
  font-size: 0.78rem;
  padding: 2px 10px;
  margin: 2px 2px 2px 0;
  color: #7aa2f7;
  text-decoration: none;
}

.ew-pill:hover { background: #2a3a68; text-decoration: none; }

/* management section heading */
.ew-mgmt-title {
  font-size: 0.88rem;
  font-weight: 600;
  color: #a0aec0;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 10px;
}

/* inline add-link / add-organizer rows */
.ew-inline-form {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.ew-inline-form input {
  flex: 1;
  min-width: 0;
  background: #111827;
  border: 1px solid #2d3561;
  border-radius: 6px;
  color: #e0e0e0;
  font: inherit;
  font-size: 0.88rem;
  padding: 5px 9px;
  outline: none;
}

.ew-inline-form input:focus { border-color: #7aa2f7; }

/* existing link rows */
.ew-link-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 0.88rem;
}

.ew-link-row .ew-btn {
  padding: 3px 10px;
  font-size: 0.78rem;
  flex-shrink: 0;
}

/* always dark even when OS prefers light */
@media (prefers-color-scheme: light) {
  body { background: #1a1a2e; color: #e0e0e0; }
}
"""


def render_bootstrap_html(*, base: str) -> str:
    """Return the OpenID bootstrap HTML for the event widget."""
    base_js = json.dumps(base)
    widget_api_src_esc = html.escape(WIDGET_API_SRC)

    bootstrap_js = f"""\
(function(){{
  var BASE = {base_js};
  var q = new URLSearchParams(window.location.search);
  var widgetId = q.get('widgetId') || 'communitybot-event';
  var roomId = q.get('roomId') || q.get('matrix_room_id') || '';
  var parentUrl = q.get('parentUrl') || '';
  function fail(m){{ document.getElementById('status').textContent = m; }}
  if (window.parent === window) {{ fail('Open this page inside a Matrix client as a widget.'); return; }}
  var origin = '*';
  try {{ if (parentUrl) origin = new URL(parentUrl).origin; }} catch (e) {{}}
  var NS = mxwidgets; if (typeof NS === 'function' && !NS.WidgetApi) NS = NS();
  var api = new NS.WidgetApi(widgetId, origin);
  api.start();
  api.requestOpenIDConnectToken().then(function(tok){{
    return fetch(BASE + '/event/widget/auth', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        openid_token: tok.access_token,
        matrix_server_name: tok.matrix_server_name,
        room_id: roomId
      }})
    }});
  }}).then(function(r){{ return r.json(); }}).then(function(d){{
    if (d && d.token) {{
      var url = BASE + '/event/?t=' + encodeURIComponent(d.token) + '&widget=1';
      if (roomId) url += '&roomId=' + encodeURIComponent(roomId);
      if (widgetId) url += '&widgetId=' + encodeURIComponent(widgetId);
      if (parentUrl) url += '&parentUrl=' + encodeURIComponent(parentUrl);
      window.location.replace(url);
    }} else {{
      fail('Authentication failed: ' + ((d && d.error) || 'unknown error'));
    }}
  }}).catch(function(e){{ fail('Could not authenticate with Matrix: ' + e); }});
}})();"""

    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n'
        f'<head>\n'
        f'<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>Event Details</title>\n'
        f'<style>\n'
        f'body {{\n'
        f'  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;\n'
        f'  background: #1a1a2e;\n'
        f'  color: #e0e0e0;\n'
        f'  display: flex;\n'
        f'  align-items: center;\n'
        f'  justify-content: center;\n'
        f'  min-height: 100dvh;\n'
        f'  margin: 0;\n'
        f'}}\n'
        f'#status {{\n'
        f'  font-size: 1rem;\n'
        f'  color: #a0aec0;\n'
        f'  text-align: center;\n'
        f'  padding: 24px;\n'
        f'}}\n'
        f'</style>\n'
        f'</head>\n'
        f'<body>\n'
        f'<div id="status">Loading event details…</div>\n'
        f'<script src="{widget_api_src_esc}"></script>\n'
        f'<script>{bootstrap_js}</script>\n'
        f'</body>\n'
        f'</html>'
    )


def render_message_html(*, title: str, message: str) -> str:
    """Return a minimal complete HTML page for errors/info."""
    title_esc = html.escape(title)
    message_esc = html.escape(message)
    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n'
        f'<head>\n'
        f'<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{title_esc}</title>\n'
        f'<style>\n'
        f'body {{\n'
        f'  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;\n'
        f'  background: #1a1a2e;\n'
        f'  color: #e0e0e0;\n'
        f'  display: flex;\n'
        f'  align-items: center;\n'
        f'  justify-content: center;\n'
        f'  min-height: 100dvh;\n'
        f'  margin: 0;\n'
        f'}}\n'
        f'.ew-msg-page {{\n'
        f'  text-align: center;\n'
        f'  padding: 32px;\n'
        f'}}\n'
        f'.ew-msg-page h1 {{\n'
        f'  font-size: 1.4rem;\n'
        f'  margin-bottom: 12px;\n'
        f'  color: #e0e0e0;\n'
        f'}}\n'
        f'.ew-msg-page p {{\n'
        f'  color: #a0aec0;\n'
        f'  font-size: 0.95rem;\n'
        f'}}\n'
        f'</style>\n'
        f'</head>\n'
        f'<body>\n'
        f'<div class="ew-msg-page">\n'
        f'<h1>{title_esc}</h1>\n'
        f'<p>{message_esc}</p>\n'
        f'</div>\n'
        f'</body>\n'
        f'</html>'
    )


def _fmt_datetime(start_ts: int, end_ts, tz_str: str) -> str:
    """Format event timestamps as a human-readable string.

    Returns "Date TBD" when start_ts == 0.
    All timestamps are milliseconds since Unix epoch (UTC).
    tz_str is used for display only — timestamps are already UTC.
    """
    if start_ts == 0:
        return "Date TBD"

    # Resolve display timezone
    tz_label = "UTC"
    display_tz = timezone.utc

    tz_norm = (tz_str or "").strip()
    if tz_norm in ("UTC", "GMT", ""):
        display_tz = timezone.utc
        tz_label = "UTC"
    elif "/" in tz_norm:
        # IANA name — try zoneinfo, fall back to UTC
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            zi = ZoneInfo(tz_norm)
            display_tz = zi
            tz_label = tz_norm.rsplit("/", 1)[-1].replace("_", " ")
            # We'll get the real abbreviation from the datetime object below
        except Exception:
            display_tz = timezone.utc
            tz_label = "UTC"
    elif tz_norm.upper() in TZ_DISPLAY_ABBREV:
        offset_str = TZ_DISPLAY_ABBREV[tz_norm.upper()]
        sign = 1 if offset_str[0] == "+" else -1
        h, m = int(offset_str[1:3]), int(offset_str[4:6])
        display_tz = timezone(timedelta(hours=sign * h, minutes=sign * m))
        tz_label = tz_norm.upper()
    else:
        display_tz = timezone.utc
        tz_label = "UTC"

    start_dt = datetime.fromtimestamp(start_ts / 1000, tz=display_tz)

    # For zoneinfo timezones, extract abbreviation from the datetime
    if "/" in tz_norm:
        abbr = start_dt.strftime("%Z")
        if abbr:
            tz_label = abbr

    def _fmt_time(dt: datetime) -> str:
        # "3:00 PM" — strip leading zero on hour
        return dt.strftime("%-I:%M %p")

    start_str = start_dt.strftime("%a, %b %-d, %Y") + " at " + _fmt_time(start_dt)

    if end_ts:
        end_dt = datetime.fromtimestamp(end_ts / 1000, tz=display_tz)
        result = f"{start_str} — {_fmt_time(end_dt)} {tz_label}"
    else:
        result = f"{start_str} {tz_label}"

    return result


def _organizer_pills(organizers: list) -> str:
    """Format a list of MXID strings as HTML pills linking to matrix.to."""
    if not organizers:
        return ""
    parts = []
    for mxid in organizers:
        mxid_esc = html.escape(mxid)
        href = html.escape(f"https://matrix.to/#/{mxid}")
        parts.append(f'<a class="ew-pill" href="{href}" target="_blank" rel="noopener">{mxid_esc}</a>')
    return "".join(parts)


def render_event_page_html(
    *,
    event_row,
    rsvp_row,
    rsvp_counts: dict,
    is_manager: bool,
    is_host: bool,
    token: str,
    room_id: str,
    widget_mode: bool,
    widget_id: str,
    parent_url: str,
    base: str,
) -> str:
    """Return a complete HTML5 event page document."""

    # --- parse JSON columns ---
    try:
        organizers = json.loads(event_row.get("organizers") or "[]") or []
    except (ValueError, TypeError):
        organizers = []
    try:
        extra_links = json.loads(event_row.get("extra_links") or "[]") or []
    except (ValueError, TypeError):
        extra_links = []

    name = event_row.get("name") or ""
    description = event_row.get("description") or ""
    event_start_ts = event_row.get("event_start_ts") or 0
    event_end_ts = event_row.get("event_end_ts") or None
    location = event_row.get("location") or ""
    host_id = event_row.get("host_id") or ""
    tz_str = event_row.get("timezone") or "UTC"
    max_additional_guests = int(event_row.get("max_additional_guests") or 0)

    # --- escape all user-controlled values ---
    name_esc = html.escape(name)
    description_esc = html.escape(description).replace("\n", "<br>")
    location_esc = html.escape(location)
    host_esc = html.escape(host_id)
    host_href = html.escape(f"https://matrix.to/#/{host_id}")
    token_esc = html.escape(token)
    room_id_esc = html.escape(room_id)
    base_esc = html.escape(base)
    widget_id_esc = html.escape(widget_id)
    parent_url_esc = html.escape(parent_url)

    # --- current RSVP ---
    cur_status = ""
    cur_guests = 0
    if rsvp_row:
        cur_status = rsvp_row.get("rsvp_status") or ""
        cur_guests = int(rsvp_row.get("guest_count") or 0)

    # --- widget nav extras ---
    widget_val = "1" if widget_mode else ""
    # hidden inputs threaded into every form
    hidden_common = (
        f'<input type="hidden" name="t" value="{token_esc}">'
        f'<input type="hidden" name="roomId" value="{room_id_esc}">'
        f'<input type="hidden" name="widget" value="{html.escape(widget_val)}">'
        f'<input type="hidden" name="widgetId" value="{widget_id_esc}">'
        f'<input type="hidden" name="parentUrl" value="{parent_url_esc}">'
    )

    # --- formatted datetime ---
    datetime_str = _fmt_datetime(event_start_ts, event_end_ts, tz_str)
    datetime_esc = html.escape(datetime_str)

    # --- stylesheet (token-gated) ---
    stylesheet = f'<link rel="stylesheet" href="{base_esc}/event/style.css?t={token_esc}">'

    # ================================================================
    # Section: header
    # ================================================================
    header_html = (
        f'<div class="ew-header">'
        f'<h1>{name_esc}</h1>'
        f'</div>'
    )

    # ================================================================
    # Section: date/time + location
    # ================================================================
    datetime_section = (
        f'<div class="ew-section">'
        f'<div class="ew-label">When</div>'
        f'<div>{datetime_esc}</div>'
    )
    if location:
        datetime_section += (
            f'<div class="ew-label" style="margin-top:8px;">Where</div>'
            f'<div>{location_esc}</div>'
        )
    datetime_section += '</div>'

    # ================================================================
    # Section: host + organizers
    # ================================================================
    organizer_pills = _organizer_pills(organizers)
    host_section = (
        f'<div class="ew-section">'
        f'<div class="ew-label">Host</div>'
        f'<div><a class="ew-pill" href="{host_href}" target="_blank" rel="noopener">{host_esc}</a></div>'
    )
    if organizer_pills:
        host_section += (
            f'<div class="ew-label" style="margin-top:8px;">Organizers</div>'
            f'<div>{organizer_pills}</div>'
        )
    host_section += '</div>'

    # ================================================================
    # Section: description
    # ================================================================
    description_section = ""
    if description:
        description_section = (
            f'<div class="ew-section">'
            f'<div class="ew-label">Description</div>'
            f'<div style="white-space:pre-wrap;font-size:0.92rem;">{description_esc}</div>'
            f'</div>'
        )

    # ================================================================
    # Section: links
    # ================================================================
    links_section = ""
    if extra_links:
        items = []
        for i, link in enumerate(extra_links, 1):
            url = link.get("url") or ""
            label = link.get("label") or url
            label_esc = html.escape(label)
            if url.startswith("http://") or url.startswith("https://"):
                url_esc = html.escape(url)
                items.append(f'<li>{i}. <a href="{url_esc}" target="_blank" rel="noopener">{label_esc}</a></li>')
            else:
                items.append(f'<li>{i}. {label_esc} ({html.escape(url)})</li>')
        links_section = (
            f'<div class="ew-section">'
            f'<div class="ew-label">Links</div>'
            f'<ul class="ew-links">{"".join(items)}</ul>'
            f'</div>'
        )

    # ================================================================
    # Section: RSVP
    # ================================================================
    if cur_status:
        guests_str = f" (+{cur_guests} guest{'s' if cur_guests != 1 else ''})" if cur_guests > 0 else ""
        status_display = f'Your RSVP: <strong>{html.escape(cur_status.title())}{html.escape(guests_str)}</strong>'
    else:
        status_display = "You have not RSVPed"

    def _btn(label, status, btn_class):
        active_class = " active" if cur_status == status else ""
        return (
            f'<button class="ew-btn {btn_class}{active_class}"'
            f' data-status="{html.escape(status)}"'
            f' onclick="doRsvp(this)">'
            f'{label}</button>'
        )

    rsvp_btns = (
        _btn("Yes", "yes", "ew-btn-yes") +
        _btn("No", "no", "ew-btn-no") +
        _btn("Maybe", "maybe", "ew-btn-maybe") +
        '<button class="ew-btn ew-btn-remove" onclick="doRsvp(this)" data-status="">'
        'Remove RSVP</button>'
    )

    # guest count selector (shown only when rsvp is yes/maybe and max > 0)
    show_guests = (cur_status in ("yes", "maybe")) and max_additional_guests > 0
    guest_display_style = "" if show_guests else ' style="display:none"'
    if max_additional_guests < 0:
        max_range = 10  # unlimited: show up to 10 as a reasonable UI cap
    else:
        max_range = max_additional_guests
    guest_options = "".join(
        f'<option value="{n}"{" selected" if n == cur_guests else ""}>{n}</option>'
        for n in range(0, max_range + 1)
    )
    guest_selector = (
        f'<div class="ew-guest-row" id="ew-guest-row"{guest_display_style}>'
        f'<label for="ew-guest-count">Additional guests:</label>'
        f'<select id="ew-guest-count">{guest_options}</select>'
        f'<button class="ew-btn ew-btn-primary" onclick="doUpdateGuests()">Update</button>'
        f'</div>'
    )
    if max_additional_guests == 0:
        guest_selector = ""

    # counts
    yes_n = rsvp_counts.get("yes", 0)
    no_n = rsvp_counts.get("no", 0)
    maybe_n = rsvp_counts.get("maybe", 0)
    yes_g = rsvp_counts.get("yes_guests", 0)
    no_g = rsvp_counts.get("no_guests", 0)
    maybe_g = rsvp_counts.get("maybe_guests", 0)
    yes_str = f"Yes: {yes_n}" + (f" (+{yes_g} guests)" if yes_g else "")
    no_str = f"No: {no_n}" + (f" (+{no_g} guests)" if no_g else "")
    maybe_str = f"Maybe: {maybe_n}" + (f" (+{maybe_g} guests)" if maybe_g else "")
    counts_display = f"{yes_str} | {no_str} | {maybe_str}"

    rsvp_section = (
        f'<div class="ew-section">'
        f'<div class="ew-label">RSVP</div>'
        f'<div class="ew-status" id="ew-cur-status">{status_display}</div>'
        f'<div class="ew-rsvp-btns">{rsvp_btns}</div>'
        f'{guest_selector}'
        f'<div class="ew-msg" id="ew-rsvp-msg"></div>'
        f'<div class="ew-counts" id="ew-counts">{html.escape(counts_display)}</div>'
        f'</div>'
    )

    # ================================================================
    # Section: ICS download
    # ================================================================
    ics_section = (
        f'<div class="ew-section">'
        f'<a href="{base_esc}/event/ics?t={token_esc}&amp;roomId={room_id_esc}" download="event.ics">'
        f'Download .ics calendar file</a>'
        f'</div>'
    )

    # ================================================================
    # Section: management (managers only)
    # ================================================================
    mgmt_section = ""
    if is_manager:
        # Resolve the event timezone to a tzinfo for pre-filling the edit form.
        # Try ZoneInfo first (DST-aware); fall back to a fixed UTC offset so that
        # abbreviations like "PST" still pre-fill the correct wall-clock time even
        # when the system has no tzdata (common in minimal Docker images).
        def _resolve_edit_tz(tz: str) -> timezone:
            tz_up = (tz or "UTC").strip().upper()
            iana = tz.strip() if "/" in tz else None
            if not iana:
                # Try to get an IANA name from the abbreviation for ZoneInfo lookup
                from community.helpers.event_utils import TZ_ABBREV_TO_IANA
                iana = TZ_ABBREV_TO_IANA.get(tz_up, "")
            if iana and iana not in ("UTC", "GMT"):
                try:
                    from zoneinfo import ZoneInfo
                    return ZoneInfo(iana)
                except Exception:
                    pass
            # Fixed-offset fallback: covers all known abbreviations
            offset_h = _ABBREV_OFFSETS.get(tz_up, 0)
            return timezone(timedelta(hours=offset_h))

        _edit_tz = _resolve_edit_tz(tz_str)

        # Build date/time defaults from event_start_ts
        if event_start_ts:
            _start_dt = datetime.fromtimestamp(event_start_ts / 1000, tz=_edit_tz)
            _date_val = _start_dt.strftime("%Y-%m-%d")
            _time_start_val = _start_dt.strftime("%H:%M")
        else:
            _date_val = ""
            _time_start_val = ""

        if event_end_ts:
            _end_dt = datetime.fromtimestamp(event_end_ts / 1000, tz=_edit_tz)
            _time_end_val = _end_dt.strftime("%H:%M")
        else:
            _time_end_val = ""

        name_val = html.escape(name)
        tz_val = html.escape(tz_str)
        loc_val = html.escape(location)
        desc_val = html.escape(description)
        mg_val = str(max_additional_guests)

        # Build existing links list for management
        mgmt_links_html = ""
        if extra_links:
            rows = []
            for i, link in enumerate(extra_links):
                lurl = html.escape(link.get("url") or "")
                llabel = html.escape(link.get("label") or link.get("url") or "")
                rows.append(
                    f'<div class="ew-link-row">'
                    f'<span>{i + 1}. {llabel} ({lurl})</span>'
                    f'<button class="ew-btn ew-btn-remove"'
                    f' onclick="doRemoveLink(this, {i})"'
                    f'>Remove</button>'
                    f'</div>'
                )
            mgmt_links_html = "".join(rows)

        mgmt_section = f"""
<div class="ew-section">
  <div class="ew-mgmt-title">Management</div>

  <!-- Edit Details -->
  <button class="ew-btn ew-btn-primary" onclick="toggleEdit()">Edit Details</button>
  <div class="ew-collapsible" id="ew-edit-panel">
    <form class="ew-form" onsubmit="doEdit(event)">
      {hidden_common}
      <label>Name<input type="text" name="name" value="{name_val}"></label>
      <label>Date<input type="date" name="date" value="{html.escape(_date_val)}"></label>
      <label>Start time<input type="time" name="start_time" value="{html.escape(_time_start_val)}"></label>
      <label>End time (optional)<input type="time" name="end_time" value="{html.escape(_time_end_val)}"></label>
      <label>Timezone
        <input type="text" name="timezone" value="{tz_val}"
               list="ew-tz-list" autocomplete="off"
               placeholder="e.g. America/Los_Angeles">
        <datalist id="ew-tz-list">
          {''.join(f'<option value="{t}">' for t in COMMON_TIMEZONES)}
        </datalist>
      </label>
      <label>Location<input type="text" name="location" value="{loc_val}"></label>
      <label>Description<textarea name="description">{desc_val}</textarea></label>
      <label>Max additional guests (-1 = unlimited, 0 = none)<input type="number" name="max_additional_guests" value="{html.escape(mg_val)}"></label>
      <button type="submit" class="ew-btn ew-btn-primary">Save Changes</button>
      <div class="ew-msg" id="ew-edit-msg"></div>
    </form>
  </div>

  <!-- Add Organizer -->
  <div style="margin-top:12px;">
    <div class="ew-label">Add Organizer</div>
    <div class="ew-inline-form">
      <input type="text" id="ew-org-input" placeholder="@user:example.org">
      <button class="ew-btn ew-btn-primary" onclick="doAddOrganizer()">Add</button>
    </div>
    <div class="ew-msg" id="ew-org-msg"></div>
  </div>

  <!-- Links management -->
  <div style="margin-top:12px;">
    <div class="ew-label">Links</div>
    <div id="ew-links-list">{mgmt_links_html}</div>
    <div class="ew-inline-form" style="margin-top:8px;">
      <input type="text" id="ew-link-url" placeholder="https://...">
      <input type="text" id="ew-link-label" placeholder="Label (optional)">
      <button class="ew-btn ew-btn-primary" onclick="doAddLink()">Add Link</button>
    </div>
    <div class="ew-msg" id="ew-link-msg"></div>
  </div>

  <!-- Cancel event -->
  <div style="margin-top:16px;">
    <button class="ew-btn ew-btn-danger" onclick="showCancel()">Cancel Event</button>
    <div class="ew-confirm-box" id="ew-cancel-box">
      <p>Are you sure you want to cancel this event? This cannot be undone.</p>
      <button class="ew-btn ew-btn-danger" onclick="doCancel()">Yes, Cancel Event</button>
      <button class="ew-btn ew-btn-remove" onclick="hideCancel()" style="margin-left:8px;">Nevermind</button>
    </div>
    <div class="ew-msg" id="ew-cancel-msg"></div>
  </div>
</div>
"""

    # ================================================================
    # JavaScript
    # ================================================================
    base_js = json.dumps(base)
    token_js = json.dumps(token)
    room_id_js = json.dumps(room_id)
    widget_js_val = json.dumps(widget_val)
    widget_id_js = json.dumps(widget_id)
    parent_url_js = json.dumps(parent_url)

    script = f"""\
<script>
(function(){{
  var BASE = {base_js};
  var T = {token_js};
  var ROOM_ID = {room_id_js};
  var WIDGET = {widget_js_val};
  var WIDGET_ID = {widget_id_js};
  var PARENT_URL = {parent_url_js};

  function qs(sel) {{ return document.querySelector(sel); }}

  function params() {{
    return {{
      t: T,
      roomId: ROOM_ID,
      widget: WIDGET,
      widgetId: WIDGET_ID,
      parentUrl: PARENT_URL
    }};
  }}

  function setMsg(el, text, isErr) {{
    if (!el) return;
    el.textContent = text;
    el.className = 'ew-msg ' + (isErr ? 'err' : 'ok');
  }}

  async function doPost(url, body, msgEl) {{
    setMsg(msgEl, '', false);
    var _t = body && body.t;
    if (_t) {{ url = url + '?t=' + encodeURIComponent(_t); }}
    try {{
      var r = await fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body)
      }});
      var d = await r.json();
      if (d && d.error) {{
        setMsg(msgEl, d.error, true);
        return null;
      }}
      return d;
    }} catch(e) {{
      setMsg(msgEl, 'Request failed: ' + e, true);
      return null;
    }}
  }}

  // ---- RSVP ----
  window.doRsvp = async function(btn) {{
    var status = btn.dataset.status || '';
    var guestEl = qs('#ew-guest-count');
    var guestCount = guestEl ? parseInt(guestEl.value, 10) : 0;
    var msgEl = qs('#ew-rsvp-msg');
    var d = await doPost(BASE + '/event/rsvp', Object.assign({{
      rsvp_status: status,
      guest_count: guestCount,
      room_id: ROOM_ID
    }}, params()), msgEl);
    if (!d) return;
    setMsg(msgEl, status ? 'RSVP saved.' : 'RSVP removed.', false);
    // Update status display
    var statusEl = qs('#ew-cur-status');
    if (statusEl) {{
      if (status) {{
        var gStr = guestCount > 0 ? ' (+' + guestCount + ' guest' + (guestCount !== 1 ? 's' : '') + ')' : '';
        statusEl.innerHTML = 'Your RSVP: <strong>' + status.charAt(0).toUpperCase() + status.slice(1) + gStr + '</strong>';
      }} else {{
        statusEl.textContent = 'You have not RSVPed';
      }}
    }}
    // Show/hide guest selector
    var guestRow = qs('#ew-guest-row');
    if (guestRow) {{
      guestRow.style.display = (status === 'yes' || status === 'maybe') ? '' : 'none';
    }}
    // Update active button
    document.querySelectorAll('.ew-rsvp-btns .ew-btn').forEach(function(b) {{
      b.classList.toggle('active', b.dataset.status === status);
    }});
    // Update counts if returned
    if (d.counts) {{
      var countsEl = qs('#ew-counts');
      if (countsEl) {{
        var c = d.counts;
        var yg = c.yes_guests || 0;
        var ng = c.no_guests || 0;
        var mg = c.maybe_guests || 0;
        countsEl.textContent =
          'Yes: ' + (c.yes || 0) + (yg ? ' (+' + yg + ' guests)' : '') + ' | ' +
          'No: ' + (c.no || 0) + (ng ? ' (+' + ng + ' guests)' : '') + ' | ' +
          'Maybe: ' + (c.maybe || 0) + (mg ? ' (+' + mg + ' guests)' : '');
      }}
    }}
  }};

  window.doUpdateGuests = async function() {{
    var activeBtn = document.querySelector('.ew-rsvp-btns .ew-btn.active[data-status]');
    if (!activeBtn || !activeBtn.dataset.status) {{
      setMsg(qs('#ew-rsvp-msg'), 'Set your RSVP status first, then update guests.', true);
      return;
    }}
    await window.doRsvp(activeBtn);
  }};

  // ---- Edit form ----
  window.toggleEdit = function() {{
    var panel = qs('#ew-edit-panel');
    if (panel) panel.classList.toggle('open');
  }};

  window.doEdit = async function(e) {{
    e.preventDefault();
    var form = e.target;
    var msgEl = qs('#ew-edit-msg');
    var body = Object.assign({{
      name: form.name.value,
      date: form.date.value,
      start_time: form.start_time.value,
      end_time: form.end_time.value,
      timezone: form.timezone.value,
      location: form.location.value,
      description: form.description.value,
      max_additional_guests: parseInt(form.max_additional_guests.value, 10),
      room_id: ROOM_ID
    }}, params());
    var d = await doPost(BASE + '/event/edit', body, msgEl);
    if (d) setMsg(msgEl, 'Event updated.', false);
  }};

  // ---- Add organizer ----
  window.doAddOrganizer = async function() {{
    var input = qs('#ew-org-input');
    var msgEl = qs('#ew-org-msg');
    if (!input || !input.value.trim()) {{ setMsg(msgEl, 'Enter an MXID.', true); return; }}
    var d = await doPost(BASE + '/event/add-organizer', Object.assign({{
      room_id: ROOM_ID,
      user_id: input.value.trim()
    }}, params()), msgEl);
    if (d) {{ setMsg(msgEl, 'Organizer added.', false); input.value = ''; }}
  }};

  // ---- Links ----
  window.doAddLink = async function() {{
    var urlEl = qs('#ew-link-url');
    var labelEl = qs('#ew-link-label');
    var msgEl = qs('#ew-link-msg');
    if (!urlEl || !urlEl.value.trim()) {{ setMsg(msgEl, 'Enter a URL.', true); return; }}
    var d = await doPost(BASE + '/event/add-link', Object.assign({{
      room_id: ROOM_ID,
      url: urlEl.value.trim(),
      label: labelEl ? labelEl.value.trim() : ''
    }}, params()), msgEl);
    if (d) {{
      setMsg(msgEl, 'Link added.', false);
      urlEl.value = '';
      if (labelEl) labelEl.value = '';
      if (d.links_html) {{ var ll = qs('#ew-links-list'); if (ll) ll.innerHTML = d.links_html; }}
    }}
  }};

  window.doRemoveLink = async function(btn, idx) {{
    var msgEl = qs('#ew-link-msg');
    var d = await doPost(BASE + '/event/remove-link', Object.assign({{
      room_id: ROOM_ID,
      link_index: idx
    }}, params()), msgEl);
    if (d) {{
      setMsg(msgEl, 'Link removed.', false);
      if (d.links_html) {{ var ll = qs('#ew-links-list'); if (ll) ll.innerHTML = d.links_html; }}
    }}
  }};

  // ---- Cancel event ----
  window.showCancel = function() {{
    var box = qs('#ew-cancel-box');
    if (box) box.classList.add('open');
  }};
  window.hideCancel = function() {{
    var box = qs('#ew-cancel-box');
    if (box) box.classList.remove('open');
  }};
  window.doCancel = async function() {{
    var msgEl = qs('#ew-cancel-msg');
    var d = await doPost(BASE + '/event/cancel', Object.assign({{
      room_id: ROOM_ID,
      confirm: true
    }}, params()), msgEl);
    if (d) setMsg(msgEl, 'Event cancelled — refresh to confirm.', false);
  }};

}})();
</script>"""

    # ================================================================
    # Assemble final document
    # ================================================================
    return (
        f'<!DOCTYPE html>\n'
        f'<html lang="en">\n'
        f'<head>\n'
        f'<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{name_esc}</title>\n'
        f'{stylesheet}\n'
        f'</head>\n'
        f'<body>\n'
        f'{header_html}\n'
        f'{datetime_section}\n'
        f'{host_section}\n'
        f'{description_section}\n'
        f'{links_section}\n'
        f'{rsvp_section}\n'
        f'{ics_section}\n'
        f'{mgmt_section}\n'
        f'{script}\n'
        f'</body>\n'
        f'</html>'
    )
