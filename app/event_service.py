from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Calendar
import recurring_ical_events

logger = logging.getLogger(__name__)
DEFAULT_ICS_FILE = Path(__file__).resolve().parent / "events.ics"


def _as_datetime(value: date | datetime, timezone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone)
        return value.astimezone(timezone)
    return datetime.combine(value, time.min, tzinfo=timezone)


def get_event_summary(
    ics_file: str | Path | None = None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Read the active/next timed event within one year; skip all-day events.

    Explicit paths override EVENT_ICS_FILE. Floating times use EVENT_TIMEZONE
    (Asia/Shanghai by default). The file is reloaded on every request.
    Missing files and empty calendars produce a frontend-compatible empty state;
    invalid calendars raise an error for the Socket.IO handler to report.
    """
    timezone = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Shanghai"))
    current = _as_datetime(now or datetime.now(timezone), timezone)
    path = Path(ics_file if ics_file is not None else os.getenv("EVENT_ICS_FILE", str(DEFAULT_ICS_FILE))).expanduser()
    empty = {"name": "暂无日程", "time": "N/A", "date": "N/A", "location": "N/A"}
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        logger.warning("ICS calendar file does not exist: %s", path)
        return empty

    calendar = Calendar.from_ical(content)
    # The library expands RRULE/RDATE and applies EXDATE/RECURRENCE-ID overrides.
    # Starting at midnight also includes today's zero-duration events.
    events = recurring_ical_events.of(calendar).between(
        current.replace(hour=0, minute=0, second=0, microsecond=0),
        current + timedelta(days=366),
    )
    candidates = []
    for event in events:
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if "DTSTART" not in event:
            continue
        raw_start = event.decoded("DTSTART")
        if not isinstance(raw_start, datetime):
            continue
        start = _as_datetime(raw_start, timezone)
        if "DTEND" in event:
            end = _as_datetime(event.decoded("DTEND"), timezone)
        else:
            end = start + event.decoded("DURATION", timedelta())
        if start >= current or end > current:
            candidates.append((start, event))
    if not candidates:
        return empty
    start, event = min(candidates, key=lambda item: item[0])
    return {
        "name": str(event.get("SUMMARY", "")) or "未命名日程",
        "time": start.strftime("%H:%M"),
        "date": start.strftime("%Y-%m-%d"),
        "location": str(event.get("LOCATION", "")) or "N/A",
    }
