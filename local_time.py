"""
Convert between stored UTC timestamps and Indiana local time.

recorded_at is stored in UTC by SQLite's datetime('now'), formatted
YYYY-MM-DD HH:MM:SS. Anything shown to a person, and anything that
means "a day" (today's order numbers, a report date), uses Indiana
time, independent of the server's own time zone setting.
"""

import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Indiana/Indianapolis")

DB_FORMAT = "%Y-%m-%d %H:%M:%S"


def local_today():
    """
    Today's date in Indiana.
    """
    return datetime.datetime.now(LOCAL_TZ).date()


def utc_bounds(local_date):
    """
    Return the stored UTC timestamps (start inclusive, end exclusive)
    covering one Indiana calendar day, for use as
    recorded_at >= start AND recorded_at < end.

    local_date may be a date or a YYYY-MM-DD string. Going through
    midnight of each day handles 23- and 25-hour DST days.
    """
    if isinstance(local_date, str):
        local_date = datetime.date.fromisoformat(local_date)

    next_date = local_date + datetime.timedelta(days=1)

    start = datetime.datetime.combine(local_date, datetime.time(), LOCAL_TZ)
    end = datetime.datetime.combine(next_date, datetime.time(), LOCAL_TZ)

    return (
        start.astimezone(datetime.UTC).strftime(DB_FORMAT),
        end.astimezone(datetime.UTC).strftime(DB_FORMAT),
    )


def to_local(utc_datetime):
    """
    Convert a naive datetime read from recorded_at (UTC) to Indiana time.
    """
    return utc_datetime.replace(tzinfo=datetime.UTC).astimezone(LOCAL_TZ)
