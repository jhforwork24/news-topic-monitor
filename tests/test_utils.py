from __future__ import annotations

from datetime import UTC, datetime

import pytest

from news_topic_monitor.utils import KST, in_window, kst_display, normalize_url, parse_datetime


def test_normalize_url_removes_tracking_and_fragment() -> None:
    value = normalize_url("HTTPS://Example.COM:443//news//1/?b=2&utm_source=x&a=1&fbclid=y#section")
    assert value == "https://example.com/news/1?a=1&b=2"


def test_parse_datetime_converts_kst_to_utc() -> None:
    parsed = parse_datetime("2026-08-15T09:00:00+09:00", now=datetime(2026, 8, 15, 12, tzinfo=UTC))
    assert parsed == datetime(2026, 8, 15, 0, tzinfo=UTC)
    assert parsed.astimezone(KST).hour == 9


def test_kst_display_converts_timezone_without_printing_abbreviation() -> None:
    assert kst_display(datetime(2026, 8, 15, 0, tzinfo=UTC)) == "2026-08-15 09:00"


def test_window_boundaries_are_half_open() -> None:
    start = datetime(2026, 8, 15, 0, tzinfo=UTC)
    end = datetime(2026, 8, 15, 6, tzinfo=UTC)
    assert in_window(start, start, end)
    assert not in_window(end, start, end)


@pytest.mark.parametrize("value", ["not-a-date", "1980-01-01T00:00:00Z"])
def test_bad_dates_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_datetime(value, now=datetime(2026, 8, 15, tzinfo=UTC))


def test_future_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="future"):
        parse_datetime("2026-08-16T00:00:00Z", now=datetime(2026, 8, 15, 0, tzinfo=UTC))
