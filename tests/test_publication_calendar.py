from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from news_topic_monitor.policy import PolicyConfigurationError
from news_topic_monitor.publication_calendar import load_skipped_publication_dates

ROOT = Path(__file__).resolve().parents[1]


def test_repository_calendar_lists_the_skipped_days_the_user_decided() -> None:
    skipped = load_skipped_publication_dates(ROOT / "config" / "publication-calendar.yaml")

    assert skipped == frozenset({date(2026, 9, 26), date(2026, 10, 10)})


def test_calendar_reads_dates_and_ignores_reasons(tmp_path) -> None:
    path = tmp_path / "calendar.yaml"
    path.write_text(
        "version: 1\nskipped_publication_dates:\n"
        "  - date: 2026-12-25\n    reason: 성탄절\n  - date: 2026-12-25\n",
        encoding="utf-8",
    )

    assert load_skipped_publication_dates(path) == frozenset({date(2026, 12, 25)})


def test_empty_list_is_a_valid_calendar(tmp_path) -> None:
    path = tmp_path / "calendar.yaml"
    path.write_text("version: 1\nskipped_publication_dates: []\n", encoding="utf-8")

    assert load_skipped_publication_dates(path) == frozenset()


@pytest.mark.parametrize(
    "content",
    [
        "version: 2\nskipped_publication_dates: []\n",
        "skipped_publication_dates: []\n",
        "version: 1\n",
        "version: 1\nskipped_publication_dates: 2026-10-10\n",
        "version: 1\nskipped_publication_dates:\n  - reason: 날짜 없음\n",
        "version: 1\nskipped_publication_dates:\n  - date: 시월 열흘\n",
        "- not\n- a mapping\n",
        "version: [unclosed\n",
    ],
)
def test_malformed_calendar_fails_closed(tmp_path, content) -> None:
    path = tmp_path / "calendar.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyConfigurationError):
        load_skipped_publication_dates(path)


def test_missing_calendar_is_a_configuration_error_not_an_empty_list(tmp_path) -> None:
    with pytest.raises(PolicyConfigurationError):
        load_skipped_publication_dates(tmp_path / "absent.yaml")
