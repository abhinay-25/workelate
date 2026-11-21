"""Timezone conversion and relative temporal resolution utilities."""

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "Asia/Kolkata"

_FIXED_FALLBACKS = {
    "UTC": timezone.utc,
    "Asia/Kolkata": timezone(timedelta(hours=5, minutes=30)),
    "Europe/London": timezone.utc,
}

WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_utc(iso_str: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp string into an aware UTC datetime."""
    cleaned = iso_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_zone(zone_name: str | None) -> tzinfo:
    """Resolve an IANA timezone name with safe fallback."""
    name = (zone_name or DEFAULT_ZONE).strip() or DEFAULT_ZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        return _FIXED_FALLBACKS.get(name, _FIXED_FALLBACKS[DEFAULT_ZONE])


def utc_to_local_dt(iso_str: str, zone_name: str | None) -> datetime:
    """Convert a UTC ISO timestamp to a person's local timezone datetime."""
    return parse_utc(iso_str).astimezone(get_zone(zone_name))


def utc_to_local_date(iso_str: str, zone_name: str | None) -> str:
    """Return YYYY-MM-DD local calendar date for a UTC ISO timestamp."""
    return utc_to_local_dt(iso_str, zone_name).date().isoformat()


def resolve_relative_due_date(text: str, ref_utc: str, zone_name: str | None) -> str | None:
    """Resolve relative deadline phrases (English and Hinglish) to YYYY-MM-DD."""
    low = (text or "").lower()
    base = utc_to_local_dt(ref_utc, zone_name).date()

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", low)
    if iso_match:
        return iso_match.group(1)

    if re.search(r"\b(?:parso(?:\s+tak)?|parson(?:\s+tak)?|day\s+after\s+tomorrow)\b", low):
        return (base + timedelta(days=2)).isoformat()

    if re.search(r"\b(?:kal\s+(?:tak|ko)|by\s+tomorrow|tomorrow|kal)\b", low):
        return (base + timedelta(days=1)).isoformat()

    if re.search(
        r"\b(?:by\s+eod\s+today|eod\s+today|aaj\s+eod(?:\s+tak)?|aaj(?:\s+tak)?|"
        r"eod\s+tak|by\s+eod|sha?am\s+tak|tonight|today)\b",
        low,
    ):
        return base.isoformat()

    next_wd = re.search(
        r"\bnext\s+(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)(?:\s+(?:tak|ko))?\b",
        low,
    )
    if next_wd:
        target = WEEKDAYS[next_wd.group(1)]
        days_ahead = (target - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (base + timedelta(days=days_ahead)).isoformat()

    by_wd = re.search(
        r"\b(?:by|on|this|before)\s+(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)\b"
        r"|\b(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs|friday|fri|saturday|sat|sunday|sun)\s+(?:tak|ko|eod)\b"
        r"|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        low,
    )
    if by_wd:
        wd_tok = by_wd.group(1) or by_wd.group(2) or by_wd.group(3)
        target = WEEKDAYS[wd_tok]
        days_ahead = (target - base.weekday()) % 7
        return (base + timedelta(days=days_ahead)).isoformat()

    return parse_explicit_date(text, base.year, base.month)


def resolve_query_weekday(weekday_name: str, ref_utc: str, zone_name: str | None) -> str | None:
    """Resolve a weekday mentioned in a question relative to the asker's local date."""
    key = (weekday_name or "").strip().lower()
    if key not in WEEKDAYS:
        return None
    base = utc_to_local_dt(ref_utc, zone_name).date()
    target = WEEKDAYS[key]
    days_ahead = (target - base.weekday()) % 7
    return (base + timedelta(days=days_ahead)).isoformat()


def parse_explicit_date(text: str, default_year: int, default_month: int | None = None) -> str | None:
    """Parse explicit day/month strings like '14 Nov 2026', 'Nov 14', or 'the 12th'."""
    low = (text or "").lower()

    m1 = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?:\s+(\d{4}))?\b",
        low,
    )
    if m1:
        day_num = int(m1.group(1))
        month_num = MONTHS[m1.group(2)]
        year_num = int(m1.group(3)) if m1.group(3) else default_year
        try:
            return date(year_num, month_num, day_num).isoformat()
        except ValueError:
            return None

    m2 = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?\b",
        low,
    )
    if m2:
        month_num = MONTHS[m2.group(1)]
        day_num = int(m2.group(2))
        year_num = int(m2.group(3)) if m2.group(3) else default_year
        try:
            return date(year_num, month_num, day_num).isoformat()
        except ValueError:
            return None

    if default_month is not None:
        m3 = re.search(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", low)
        if m3:
            day_num = int(m3.group(1))
            try:
                return date(default_year, default_month, day_num).isoformat()
            except ValueError:
                return None

    return None
