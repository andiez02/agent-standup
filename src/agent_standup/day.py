from datetime import date, datetime, timedelta, timezone, tzinfo


def previous_workday(today: date, holidays: frozenset[date] = frozenset()) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5 or d in holidays:
        d -= timedelta(days=1)
    return d


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def to_local(ts: datetime, tz: tzinfo | None) -> datetime:
    return ts.astimezone(tz) if tz else ts.astimezone()


def local_tz(tz: tzinfo | None) -> tzinfo:
    return tz or datetime.now().astimezone().tzinfo
