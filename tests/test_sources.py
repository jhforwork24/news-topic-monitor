from __future__ import annotations

from news_topic_monitor.sources import (
    DISABILITY_PRESS_SOURCES,
    DISABILITY_SECTION_ALLOWED_SOURCES,
    LABOR_ALTERNATIVE_SOURCES,
    LABOR_SECTION_ALLOWED_SOURCES,
    PRIMARY_COMPARISON_SOURCES,
    SOURCE_CAMP,
    SOURCE_LABELS,
)


def test_thirteen_designated_outlets_split_into_four_groups() -> None:
    designated = DISABILITY_PRESS_SOURCES | LABOR_ALTERNATIVE_SOURCES | PRIMARY_COMPARISON_SOURCES
    assert len(designated) == 13
    assert len(DISABILITY_PRESS_SOURCES) == 3
    assert len(LABOR_ALTERNATIVE_SOURCES) == 2
    assert len(PRIMARY_COMPARISON_SOURCES) == 8
    conservative = {"chosun", "joongang", "donga"}
    progressive = {"khan", "hani", "pressian", "ohmynews", "sisain"}
    assert conservative | progressive == PRIMARY_COMPARISON_SOURCES
    assert len(progressive) == 5


def test_disability_section_allows_eleven_designated_outlets() -> None:
    assert len(DISABILITY_SECTION_ALLOWED_SOURCES) == 11
    expected = DISABILITY_PRESS_SOURCES | PRIMARY_COMPARISON_SOURCES
    assert expected == DISABILITY_SECTION_ALLOWED_SOURCES
    assert LABOR_ALTERNATIVE_SOURCES.isdisjoint(DISABILITY_SECTION_ALLOWED_SOURCES)


def test_labor_section_allows_eleven_designated_outlets() -> None:
    assert len(LABOR_SECTION_ALLOWED_SOURCES) == 11
    assert "beminor" in LABOR_SECTION_ALLOWED_SOURCES
    assert "ablenews" not in LABOR_SECTION_ALLOWED_SOURCES
    assert "theindigo" not in LABOR_SECTION_ALLOWED_SOURCES


def test_sisain_is_registered_with_a_label_and_progressive_camp() -> None:
    assert SOURCE_LABELS["sisain"] == "시사인"
    assert SOURCE_CAMP["sisain"] == "진보"
