from datetime import date, datetime, timezone, timedelta

from agent_standup.day import parse_ts, previous_workday, to_local

ICT = timezone(timedelta(hours=7))


def test_monday_reports_friday():
    assert previous_workday(date(2026, 10, 12)) == date(2026, 10, 9)


def test_tuesday_reports_monday():
    assert previous_workday(date(2026, 10, 13)) == date(2026, 10, 12)


def test_sunday_run_reports_friday():
    assert previous_workday(date(2026, 10, 11)) == date(2026, 10, 9)


def test_holiday_is_skipped():
    holidays = frozenset({date(2026, 10, 9)})
    assert previous_workday(date(2026, 10, 12), holidays) == date(2026, 10, 8)


def test_parse_ts_zulu():
    assert parse_ts("2026-10-08T16:30:00.123Z") == datetime(2026, 10, 8, 16, 30, 0, 123000, tzinfo=timezone.utc)


def test_parse_ts_naive_is_utc():
    assert parse_ts("2026-10-08T16:30:00").tzinfo == timezone.utc


def test_parse_ts_rejects_garbage():
    assert parse_ts("yesterday") is None
    assert parse_ts(None) is None
    assert parse_ts(12345) is None


def test_to_local_crosses_midnight():
    ts = parse_ts("2026-10-08T17:30:00Z")
    assert to_local(ts, ICT).date() == date(2026, 10, 9)
