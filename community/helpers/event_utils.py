"""Utilities for community event creation, formatting, and permissions."""

import json
import re
from datetime import datetime, date, time, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple

from mautrix.client import Client
from mautrix.types import UserID

# Default timezone when none is set (fallback behavior)
DEFAULT_TIMEZONE = "UTC"

# Use stdlib UTC so we don't require tzdata for UTC (ZoneInfo("UTC") fails when tzdata is missing)
UTC_TZ = timezone.utc

from zoneinfo import ZoneInfo

# Common timezone abbreviations -> IANA timezone name (for parsing --time "15:00 PST")
# Abbreviations are ambiguous; we map to a reasonable default (e.g. US zones).
TZ_ABBREV_TO_IANA: Dict[str, str] = {
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "AKST": "America/Anchorage",
    "AKDT": "America/Anchorage",
    "HST": "Pacific/Honolulu",
    "UTC": "UTC",
    "GMT": "UTC",
    "BST": "Europe/London",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
}

# Fixed UTC offsets (in hours) for common abbreviations.
# This avoids requiring tzdata for these zones.
TZ_ABBREV_OFFSETS: Dict[str, int] = {
    "PST": -8,
    "PDT": -7,
    "MST": -7,
    "MDT": -6,
    "CST": -6,
    "CDT": -5,
    "EST": -5,
    "EDT": -4,
    "AKST": -9,
    "AKDT": -8,
    "HST": -10,
    "GMT": 0,
    "BST": 1,
    "CET": 1,
    "CEST": 2,
    "AEST": 10,
    "AEDT": 11,
}

# RSVP status reaction keys (with and without the emoji variation selector)
RSVP_YES_KEYS = {"👍", "👍️"}
RSVP_NO_KEYS = {"👎", "👎️"}
RSVP_MAYBE_KEYS = {"🤔", "🤔️"}

# Additional guests are indicated with keycap number reactions 1️⃣..9️⃣. A user's
# guest count is the value of their most-recent active number reaction; removing
# (redacting) it drops the count. There is deliberately no 0️⃣ — "no guests" is
# simply the absence of a number reaction.
MAX_SEEDED_GUEST_REACTIONS = 9
# keycap emoji = digit + optional VS16 (U+FE0F) + combining enclosing keycap (U+20E3)
_KEYCAP_RE = re.compile("^([1-9])️?⃣$")


def guest_count_from_reaction_key(key: str) -> Optional[int]:
    """Return the guest count (1-9) for a keycap number reaction, else None."""
    m = _KEYCAP_RE.match((key or "").strip())
    return int(m.group(1)) if m else None


def guest_reaction_key(n: int) -> str:
    """The canonical keycap reaction string for guest count n (1-9)."""
    return f"{n}️⃣"


def seed_reaction_keys(max_additional_guests: int) -> List[str]:
    """Reactions the bot should seed on an event description: the status
    reactions, plus keycap number reactions up to the event's guest cap
    (capped at MAX_SEEDED_GUEST_REACTIONS; none when guests are disallowed)."""
    keys = ["👍", "👎", "🤔"]
    if max_additional_guests == 0:
        return keys
    top = MAX_SEEDED_GUEST_REACTIONS
    if max_additional_guests > 0:
        top = min(max_additional_guests, MAX_SEEDED_GUEST_REACTIONS)
    keys.extend(guest_reaction_key(i) for i in range(1, top + 1))
    return keys


def parse_organizers_json(raw: str) -> List[str]:
    """Parse organizers column (JSON array of user IDs)."""
    if not raw or raw == "[]":
        return []
    try:
        out = json.loads(raw)
        return list(out) if isinstance(out, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def parse_extra_links_json(raw: str) -> List[Dict[str, str]]:
    """Parse extra_links column (JSON array of {label, url})."""
    if not raw or raw == "[]":
        return []
    try:
        out = json.loads(raw)
        if not isinstance(out, list):
            return []
        return [
            {"label": x.get("label", "Link"), "url": x.get("url", "")}
            for x in out
            if isinstance(x, dict) and x.get("url")
        ]
    except (json.JSONDecodeError, TypeError):
        return []


def _normalize_url(url: str) -> str:
    """Normalize a URL for dedupe comparison (trim + case-fold)."""
    return (url or "").strip().casefold()


def add_link(
    links: List[Dict[str, str]], url: str, label: Optional[str] = None
) -> Tuple[List[Dict[str, str]], bool]:
    """Append a link, de-duplicating by URL. If the URL already exists, its label
    is updated instead of adding a duplicate.

    Returns (new_links, added) where added is False if it updated an existing entry.
    """
    url = (url or "").strip()
    label = (label or "Link").strip() or "Link"
    out = [dict(x) for x in links]
    for entry in out:
        if _normalize_url(entry.get("url", "")) == _normalize_url(url):
            entry["label"] = label
            return out, False
    out.append({"label": label, "url": url})
    return out, True


def remove_link(
    links: List[Dict[str, str]], selector: str
) -> Tuple[List[Dict[str, str]], Optional[Dict[str, str]]]:
    """Remove a link by 1-based index or by matching URL/label.

    Returns (new_links, removed_entry). removed_entry is None if nothing matched.
    """
    selector = (selector or "").strip()
    if not selector:
        return list(links), None
    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(links):
            out = list(links)
            removed = out.pop(idx - 1)
            return out, removed
        return list(links), None
    for match_key in ("url", "label"):
        for i, entry in enumerate(links):
            if (entry.get(match_key, "") or "").casefold() == selector.casefold():
                out = list(links)
                removed = out.pop(i)
                return out, removed
    return list(links), None


def edit_link(
    links: List[Dict[str, str]],
    selector: str,
    new_url: Optional[str] = None,
    new_label: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], Optional[Dict[str, str]]]:
    """Edit a link's url and/or label, selected by 1-based index or URL/label match.

    Returns (new_links, edited_entry). edited_entry is None if nothing matched.
    """
    selector = (selector or "").strip()
    out = [dict(x) for x in links]
    target = None
    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(out):
            target = out[idx - 1]
    else:
        for entry in out:
            if (
                (entry.get("url", "") or "").casefold() == selector.casefold()
                or (entry.get("label", "") or "").casefold() == selector.casefold()
            ):
                target = entry
                break
    if target is None:
        return out, None
    if new_url and new_url.strip():
        target["url"] = new_url.strip()
    if new_label and new_label.strip():
        target["label"] = new_label.strip()
    return out, target


def format_links_list(links: List[Dict[str, str]]) -> str:
    """Human-readable numbered list of links (HTML) for the list-links command."""
    if not links:
        return "No links attached to this event."
    rows = [
        f'{i}. <a href="{link["url"]}">{link["label"]}</a> ({link["url"]})'
        for i, link in enumerate(links, 1)
    ]
    return "<br/>".join(rows)


def _resolve_timezone(tz_str: str, date_obj: Optional[date] = None):
    """Resolve timezone string (IANA or abbreviation) to tzinfo.

    - UTC/GMT uses stdlib timezone.utc so tzdata is not required.
    - Common abbreviations (PST, EST, CET, AEST, etc) use fixed UTC offsets
      so they work even when tzdata is missing.
    - IANA names (America/Los_Angeles, Europe/Paris, ...) use ZoneInfo when available,
      otherwise fall back to None (caller may then fall back to UTC).
    """
    if not tz_str or not tz_str.strip():
        return None
    tz_str = tz_str.strip()
    upper = tz_str.upper()
    # UTC/GMT: use stdlib so ZoneInfo/tzdata is not required
    if upper in ("UTC", "GMT"):
        return UTC_TZ
    # Fixed-offset abbreviation (no tzdata required)
    if upper in TZ_ABBREV_OFFSETS:
        offset_hours = TZ_ABBREV_OFFSETS[upper]
        return timezone(timedelta(hours=offset_hours))
    # Try as IANA first (e.g. America/Los_Angeles).
    # Normalize common lowercase forms like "america/los_angeles".
    try:
        return ZoneInfo(tz_str)
    except Exception:
        pass
    if "/" in tz_str:
        parts = tz_str.split("/")
        # Normalize each path and underscore segment: "america/los_angeles" -> "America/Los_Angeles"
        norm_parts = []
        for p in parts:
            sub = p.split("_")
            sub_norm = "_".join(s[:1].upper() + s[1:] for s in sub if s)
            norm_parts.append(sub_norm)
        norm = "/".join(norm_parts)
        if norm != tz_str:
            try:
                return ZoneInfo(norm)
            except Exception:
                pass
        # Approximate common US IANA zones without tzdata using DST heuristics.
        if date_obj is not None:
            month = date_obj.month
            is_dst_month = 3 <= month <= 10
            key = norm.lower()
            if key in ("america/los_angeles", "us/pacific"):
                offset_hours = -7 if is_dst_month else -8
                return timezone(timedelta(hours=offset_hours))
            if key in ("america/denver", "us/mountain"):
                offset_hours = -6 if is_dst_month else -7
                return timezone(timedelta(hours=offset_hours))
            if key in ("america/chicago", "us/central"):
                offset_hours = -5 if is_dst_month else -6
                return timezone(timedelta(hours=offset_hours))
            if key in ("america/new_york", "us/eastern"):
                offset_hours = -4 if is_dst_month else -5
                return timezone(timedelta(hours=offset_hours))
    # Try abbreviation mapped to IANA (for completeness when tzdata is available)
    iana = TZ_ABBREV_TO_IANA.get(upper)
    if iana:
        if iana.upper() == "UTC":
            return UTC_TZ
        try:
            return ZoneInfo(iana)
        except Exception:
            pass
    return None


# Time parse error hint. Accepted: single time or span "START - END"; optional am/pm; optional TZ.
TIME_FORMAT_HINT = (
    "Use HH:MM or H:MM (24-hour), or a span like 10:00 - 18:00 or 10:00AM - 6:00PM PST. "
    "You can also use IANA zones like America/Los_Angeles."
)

# am/pm (case insensitive) - not a timezone; strip and convert 12h -> 24h
_AM_PM_RE = re.compile(r"\s*(am|pm)\s*$", re.IGNORECASE)


def parse_time_with_timezone(
    time_str: str,
    date_obj: date,
    default_tz: str = DEFAULT_TIMEZONE,
) -> Tuple[int, str]:
    """Parse a time string with optional timezone (e.g. '15:00', '15:00 PST', '3:00pm PST').

    Time is interpreted in the given (or default) timezone, then converted to UTC for storage.
    Returns (utc_timestamp_ms, timezone_str for storage — IANA name e.g. America/Los_Angeles).
    """
    if not time_str or not time_str.strip():
        raise ValueError(f"Time string is empty. {TIME_FORMAT_HINT}")
    raw = time_str.strip()
    tz_suffix = None
    # Strip optional am/pm and convert 12h -> 24h (do not treat am/pm as timezone)
    is_pm = False
    is_am = False
    am_pm_match = _AM_PM_RE.search(raw)
    if am_pm_match:
        raw = raw[: am_pm_match.start()].strip()
        if am_pm_match.group(1).lower() == "pm":
            is_pm = True
        else:
            is_am = True
    # Match time part (HH:MM or H:MM or HH:MM:SS or HHMM or bare hour); remainder may be "PM PST" or "PST"
    time_part_re = re.match(
        r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*",
        raw,
    )
    if time_part_re:
        h = int(time_part_re.group(1))
        m = int(time_part_re.group(2))
        sec = int(time_part_re.group(3)) if time_part_re.group(3) else 0
        rest_after = raw[time_part_re.end() :].strip()
        if rest_after:
            words = rest_after.split(None, 1)  # first word, optional rest
            if words and words[0].upper() in ("AM", "PM"):
                if words[0].upper() == "PM":
                    is_pm = True
                else:
                    is_am = True
                tz_suffix = words[1] if len(words) > 1 else None
            elif rest_after[0].isalpha():
                tz_suffix = rest_after
    else:
        # Try without colon: 1500 or 1500PST
        time_part_re = re.match(r"^(\d{1,2})(\d{2})\s*", raw)
        if time_part_re:
            h, m = int(time_part_re.group(1)), int(time_part_re.group(2))
            sec = 0
            rest_after = raw[time_part_re.end() :].strip()
            if rest_after and rest_after[0].isalpha():
                tz_suffix = rest_after
        else:
            # Handle bare hour with inline am/pm and optional timezone,
            # e.g. "6pm America/Los_Angeles" or "6pm PST".
            ampm_match = re.match(r"^(\d{1,2})(am|pm)\b\s*(.*)$", raw, re.IGNORECASE)
            if ampm_match:
                h = int(ampm_match.group(1))
                m = 0
                sec = 0
                if ampm_match.group(2).lower() == "pm":
                    is_pm = True
                else:
                    is_am = True
                rest_after = (ampm_match.group(3) or "").strip()
                if rest_after and rest_after[0].isalpha():
                    tz_suffix = rest_after
            else:
                # Finally, allow bare hour like "6" or "6 america/los_angeles"
                time_part_re = re.match(r"^(\d{1,2})\s*", raw)
                if time_part_re:
                    h = int(time_part_re.group(1))
                    m = 0
                    sec = 0
                    rest_after = raw[time_part_re.end() :].strip()
                    if rest_after and rest_after[0].isalpha():
                        tz_suffix = rest_after
                else:
                    raise ValueError(f"Could not parse time {raw!r}. {TIME_FORMAT_HINT}")

    if is_pm:
        if h < 12:
            h += 12
    elif is_am:
        if h == 12:
            h = 0

    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59):
        raise ValueError(
            f"Invalid time {h}:{m}:{sec} (hour 0–23, minute 0–59). {TIME_FORMAT_HINT}"
        )

    # Resolve timezone: explicit suffix, else default (UTC)
    if tz_suffix:
        zone = _resolve_timezone(tz_suffix, date_obj=date_obj)
        if zone is not None:
            # Store a stable identifier for the timezone we resolved:
            # - For known abbreviations, keep the abbreviation (e.g. PST)
            # - For IANA names, keep the original string
            upper = tz_suffix.upper()
            if upper in TZ_ABBREV_OFFSETS:
                store_tz = upper
            else:
                store_tz = tz_suffix
        else:
            zone = UTC_TZ
            store_tz = DEFAULT_TIMEZONE
    else:
        zone = _resolve_timezone(default_tz, date_obj=date_obj)
        if zone is None:
            zone = UTC_TZ
            store_tz = DEFAULT_TIMEZONE
        else:
            store_tz = default_tz

    local_dt = datetime.combine(date_obj, time(h, m, sec), tzinfo=zone)
    utc_ts_ms = int(local_dt.timestamp() * 1000)
    return utc_ts_ms, store_tz


# Time span separator: "10:00 - 18:00" or "10:00AM - 6:00PM PST"
TIME_SPAN_SEP = " - "


def parse_time_span_with_timezone(
    time_str: str,
    date_obj: date,
    default_tz: str = DEFAULT_TIMEZONE,
) -> Tuple[int, int, str]:
    """Parse a single time or a span 'START - END' (e.g. '10:00 - 18:00' or '10:00AM - 6:00PM PST').

    Returns (start_ts_ms, end_ts_ms, timezone_str). If no span, end = start + 1 hour.
    """
    raw = (time_str or "").strip()
    if not raw:
        raise ValueError(f"Time string is empty. {TIME_FORMAT_HINT}")
    if TIME_SPAN_SEP in raw:
        parts = raw.split(TIME_SPAN_SEP, 1)
        start_part = parts[0].strip()
        end_part = parts[1].strip() if len(parts) > 1 else ""
        if not start_part or not end_part:
            raise ValueError(
                f"Time span must be START - END (e.g. 10:00 - 18:00 or 10:00AM - 6:00PM PST). {TIME_FORMAT_HINT}"
            )
        # Parse end first so its timezone (e.g. "6:00 PM PST") applies to the whole span
        end_ts_ms, store_tz = parse_time_with_timezone(
            end_part, date_obj, default_tz=default_tz
        )
        start_ts_ms, _ = parse_time_with_timezone(
            start_part, date_obj, default_tz=store_tz
        )
        if end_ts_ms <= start_ts_ms:
            raise ValueError(
                "End time must be after start time in a time span (e.g. 10:00 - 18:00)."
            )
        return start_ts_ms, end_ts_ms, store_tz
    start_ts_ms, store_tz = parse_time_with_timezone(raw, date_obj, default_tz=default_tz)
    return start_ts_ms, start_ts_ms + 3600 * 1000, store_tz


def _format_datetime(
    start_ts: int,
    end_ts: Optional[int],
    timezone_str: Optional[str] = None,
) -> str:
    """Format start (and optionally end) timestamp for display in the event's timezone."""
    tz = (timezone_str or DEFAULT_TIMEZONE).strip()
    # Use the event's own date for DST heuristics when approximating without tzdata
    event_date = datetime.fromtimestamp(start_ts / 1000.0, tz=UTC_TZ).date()
    zone = _resolve_timezone(tz, date_obj=event_date)
    if zone is None:
        zone = UTC_TZ
        tz = DEFAULT_TIMEZONE
    start_dt = datetime.fromtimestamp(start_ts / 1000.0, tz=zone)
    # Show friendly label: UTC for default, otherwise IANA or abbreviation
    label = "UTC" if tz.upper() == "UTC" else tz
    s = start_dt.strftime("%Y-%m-%d %H:%M") + f" {label}"
    if end_ts and end_ts > start_ts:
        end_dt = datetime.fromtimestamp(end_ts / 1000.0, tz=zone)
        s += f" – {end_dt.strftime('%Y-%m-%d %H:%M')} {label}"
    return s


def is_utc_default(timezone_str: Optional[str]) -> bool:
    """True if the event is using UTC as the default (fallback) timezone."""
    return not timezone_str or (timezone_str.strip().upper() == "UTC")


def get_event_timezone(row: Any) -> str:
    """Get timezone from an event row (works with dict, sqlite3.Row, asyncpg.Record)."""
    try:
        return row["timezone"] or DEFAULT_TIMEZONE
    except (KeyError, TypeError, IndexError):
        return DEFAULT_TIMEZONE


def guest_policy_text(max_additional_guests: int) -> str:
    """One-line human description of the additional-guest policy."""
    if max_additional_guests == 0:
        return "No additional guests."
    if max_additional_guests < 0:
        return "Additional guests: no limit."
    if max_additional_guests == 1:
        return "Additional guests: up to 1 per attendee."
    return f"Additional guests: up to {max_additional_guests} per attendee."


def guest_rsvp_instructions(max_additional_guests: int) -> str:
    """RSVP instructions describing status reactions and the guest-number
    reactions permitted by the event's cap."""
    base = "👍 yes, 👎 no, 🤔 maybe"
    if max_additional_guests == 0:
        return base + ". This event does not allow additional guests."
    top = MAX_SEEDED_GUEST_REACTIONS
    if max_additional_guests > 0:
        top = min(max_additional_guests, MAX_SEEDED_GUEST_REACTIONS)
    extra = (
        f". To bring guests, also react with a number 1️⃣–{top}️⃣ "
        "for how many additional people you're bringing (remove the reaction to undo)."
    )
    if max_additional_guests < 0 or max_additional_guests > MAX_SEEDED_GUEST_REACTIONS:
        extra += f" (limit {'no limit' if max_additional_guests < 0 else max_additional_guests})."
    return base + extra


def format_event_topic(
    name: str,
    start_ts: int,
    end_ts: Optional[int],
    location: Optional[str],
    host_id: str,
    organizers: List[str],
    description: Optional[str],
    extra_links: List[Dict[str, str]],
    room_link: str,
    timezone_str: Optional[str] = None,
    max_additional_guests: int = 1,
) -> str:
    """Build room topic text from event fields."""
    tz = timezone_str or DEFAULT_TIMEZONE
    lines = [f"Event: {name}", f"Date/Time: {_format_datetime(start_ts, end_ts, tz)}"]
    if is_utc_default(timezone_str):
        lines.append("(Time is in UTC. Set timezone with: !community event update <room> --time HH:MM TZ)")
    if location:
        lines.append(f"Location: {location}")
    lines.append(f"Host: {host_id}")
    if organizers:
        lines.append(f"Organizers: {', '.join(organizers)}")
    if description:
        lines.append(f"Description: {description}")
    for link in extra_links:
        lines.append(f"{link['label']}: {link['url']}")
    lines.append(guest_policy_text(max_additional_guests))
    lines.append(f"Room: {room_link}")
    return " | ".join(lines)


def format_event_description_html(
    name: str,
    start_ts: int,
    end_ts: Optional[int],
    location: Optional[str],
    host_id: str,
    organizers: List[str],
    description: Optional[str],
    extra_links: List[Dict[str, str]],
    room_link: str,
    room_id: str,
    timezone_str: Optional[str] = None,
    max_additional_guests: int = 1,
) -> str:
    """Build HTML description for the event (for describe command and room topic)."""
    tz = timezone_str or DEFAULT_TIMEZONE
    date_str = _format_datetime(start_ts, end_ts, tz)
    parts = [
        f"<b>{name}</b>",
        f"📅 {date_str}",
    ]
    if is_utc_default(timezone_str):
        parts.append(
            "<i>Time is in UTC (default). Set the correct timezone with: "
            "!community event update &lt;room&gt; --time HH:MM TZ</i>"
        )
    if location:
        parts.append(f"📍 {location}")
    parts.append(f"Host: {host_id}")
    if organizers:
        parts.append(f"Organizers: {', '.join(organizers)}")
    if description:
        parts.append(f"<br/>{description}")
    if extra_links:
        parts.append("<br/>Links:")
        for link in extra_links:
            parts.append(f' • <a href="{link["url"]}">{link["label"]}</a>')
    # RSVP instructions and event room link
    parts.append(
        "<br/><i>Use the reactions on this message to RSVP:</i> "
        + guest_rsvp_instructions(max_additional_guests)
        + " Yes or maybe responses will be invited to the event room."
    )
    parts.append(f'<br/><a href="https://matrix.to/#/{room_id}">Join event room</a>')
    return "<br/>".join(parts)


def format_event_description_text(
    name: str,
    start_ts: int,
    end_ts: Optional[int],
    location: Optional[str],
    host_id: str,
    organizers: List[str],
    description: Optional[str],
    extra_links: List[Dict[str, str]],
    room_id: str,
    timezone_str: Optional[str] = None,
    max_additional_guests: int = 1,
) -> str:
    """Plaintext fallback for the event description message (used as the m.text
    body alongside the HTML formatted_body, including for edits)."""
    tz = timezone_str or DEFAULT_TIMEZONE
    lines = [name, _format_datetime(start_ts, end_ts, tz)]
    if location:
        lines.append(f"Location: {location}")
    lines.append(f"Host: {host_id}")
    if organizers:
        lines.append(f"Organizers: {', '.join(organizers)}")
    if description:
        lines.append(description)
    for link in extra_links:
        lines.append(f"{link['label']}: {link['url']}")
    lines.append("RSVP with reactions: " + guest_rsvp_instructions(max_additional_guests))
    lines.append(f"Event room: https://matrix.to/#/{room_id}")
    return "\n".join(lines)


def generate_ics(
    name: str,
    start_ts: int,
    end_ts: Optional[int],
    location: Optional[str],
    description: Optional[str],
    room_id: str,
    uid_suffix: str,
) -> str:
    """Generate .ics file content (VCALENDAR with one VEVENT)."""
    start_dt = datetime.fromtimestamp(start_ts / 1000.0, tz=UTC_TZ)
    end_ts_use = end_ts if (end_ts and end_ts > start_ts) else start_ts + 3600 * 1000
    end_dt = datetime.fromtimestamp(end_ts_use / 1000.0, tz=UTC_TZ)
    # DTSTAMP is when the calendar object was created, not the event start.
    dtstamp = datetime.now(UTC_TZ)

    def ics_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    uid = f"community-event-{uid_suffix}@{room_id.replace('!', '').replace(':', '-')}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CommunityBot//Event//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{ics_escape(name)}",
    ]
    if location:
        lines.append(f"LOCATION:{ics_escape(location)}")
    desc = description or f"Matrix event room: https://matrix.to/#/{room_id}"
    lines.append(f"DESCRIPTION:{ics_escape(desc)}")
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


async def resolve_room_id(
    client: Client, room_arg: Optional[str], current_room_id: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve room argument to room_id. room_arg can be alias (#foo:server) or room ID.

    Returns:
        (room_id, error_message). If error_message is set, room_id is None.
    """
    if not room_arg or not room_arg.strip():
        return (current_room_id, None)
    room_arg = room_arg.strip()
    if room_arg.startswith("#"):
        try:
            result = await client.resolve_room_alias(room_arg)
            return (result["room_id"], None)
        except Exception as e:
            return (None, f"Could not resolve room alias: {e}")
    return (room_arg, None)


def rsvp_status_from_reaction_key(key: str) -> Optional[str]:
    """Map a reaction key to an RSVP status ('yes'|'no'|'maybe'), else None."""
    key_stripped = (key or "").strip()
    if key_stripped in RSVP_YES_KEYS:
        return "yes"
    if key_stripped in RSVP_NO_KEYS:
        return "no"
    if key_stripped in RSVP_MAYBE_KEYS:
        return "maybe"
    return None


def classify_reaction(key: str) -> Optional[Tuple[str, Any]]:
    """Classify an RSVP reaction key.

    Returns ('status', 'yes'|'no'|'maybe') for a status reaction,
    ('guest', n) for a keycap number reaction (n in 1..9), or None otherwise.
    """
    status = rsvp_status_from_reaction_key(key)
    if status is not None:
        return ("status", status)
    guests = guest_count_from_reaction_key(key)
    if guests is not None:
        return ("guest", guests)
    return None


def compute_rsvp_from_reactions(
    reactions: List[Dict[str, Any]]
) -> Optional[Tuple[str, int]]:
    """Derive a user's RSVP from their active tracked reactions.

    Each reaction is a mapping with at least 'key' and 'created_ts'. The status
    is taken from the most-recent status reaction and the guest count from the
    most-recent keycap number reaction (0 if none).

    Returns (status, guest_count), or None if there is no active status reaction
    (in which case the user has no effective RSVP and their summary row, if any,
    should be removed).
    """
    latest_status = None
    latest_status_ts = None
    latest_guests = 0
    latest_guests_ts = None
    for r in reactions:
        ts = r.get("created_ts") or 0
        kind = classify_reaction(r.get("key", ""))
        if not kind:
            continue
        if kind[0] == "status":
            if latest_status_ts is None or ts >= latest_status_ts:
                latest_status_ts = ts
                latest_status = kind[1]
        else:  # guest
            if latest_guests_ts is None or ts >= latest_guests_ts:
                latest_guests_ts = ts
                latest_guests = kind[1]
    if latest_status is None:
        return None
    return (latest_status, latest_guests)


def sanitize_event_name(name: str) -> str:
    """Sanitize event name for use in room alias localpart."""
    return re.sub(r"[^a-zA-Z0-9]", "", name).lower()
