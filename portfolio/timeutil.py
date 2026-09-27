from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_ny() -> date:
    return datetime.now(NY).date()


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands datetimes back naive; everything we store is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ny_date(dt: datetime | None) -> date | None:
    return aware(dt).astimezone(NY).date() if dt else None


def trade_day(dt: datetime | None) -> date | None:
    """Brokers send date-only values as midnight UTC; keep those as-is, put real timestamps on NY time."""
    if dt is None:
        return None
    dt = aware(dt)
    utc = dt.astimezone(timezone.utc)
    if (utc.hour, utc.minute, utc.second, utc.microsecond) == (0, 0, 0, 0):
        return utc.date()
    return dt.astimezone(NY).date()


def parse_dt(value) -> datetime | None:
    """ISO strings from APIs ('2026-09-25', '2026-09-25T14:03:00Z', '...+00:00') -> aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return aware(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    try:
        return aware(datetime.fromisoformat(s))
    except ValueError:
        pass
    try:
        return aware(datetime.fromisoformat(s[:10]))
    except ValueError:
        return None
