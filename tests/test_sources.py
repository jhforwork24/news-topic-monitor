from __future__ import annotations

from news_topic_monitor.models import AuditSeverity
from news_topic_monitor.sources import (
    DISABILITY_PRESS_SOURCES,
    DISABILITY_SECTION_ALLOWED_SOURCES,
    LABOR_ALTERNATIVE_SOURCES,
    LABOR_SECTION_ALLOWED_SOURCES,
    PRIMARY_COMPARISON_SOURCES,
    SOURCE_CAMP,
    SOURCE_LABELS,
    monitored_personnel_match,
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


def test_personnel_match_requires_institution_position_and_event_term_together() -> None:
    # "보건복지부 장관은 오늘 ~라고 밝혔다" runs almost daily and is not personnel news —
    # institution + position alone must not match.
    routine = "보건복지부 장관은 오늘 발달장애인 지원 대책을 밝혔다."
    assert monitored_personnel_match(routine) is None

    appointment = "보건복지부, 신임 장관 후보자에 OOO 지명"
    target = monitored_personnel_match(appointment)
    assert target is not None
    assert target.label == "보건복지부 장관"
    assert target.severity == AuditSeverity.FATAL


def test_personnel_match_distinguishes_bureau_anchors_without_the_parent_ministry() -> None:
    # "장애인정책국"/"통합고용정책국" are unique to their own ministry, so the article
    # does not need to spell out "보건복지부"/"고용노동부" again.
    welfare = monitored_personnel_match("복지부 장애인정책국장에 OOO 내정")
    assert welfare is not None
    assert welfare.label == "보건복지부 장애인정책국 국장"

    labor = monitored_personnel_match("고용노동부 통합고용정책국장 취임")
    assert labor is not None
    assert labor.label == "고용노동부 통합고용정책국 국장"


def test_personnel_match_severity_splits_by_position_tier() -> None:
    fatal = monitored_personnel_match("한국장애인개발원 신임 원장 취임")
    assert fatal is not None
    assert fatal.severity == AuditSeverity.FATAL

    warning = monitored_personnel_match("한국장애인개발원 본부장 인사 발령, OOO 선임")
    assert warning is not None
    assert warning.severity == AuditSeverity.WARNING


def test_personnel_match_is_none_without_an_event_term() -> None:
    # 기관+직위만으로는 매칭하지 않는다 — 오탐 방지가 이 기능의 핵심 제약이다.
    assert monitored_personnel_match("한국장애인고용공단 이사장이 국정감사에 출석했다") is None


def test_personnel_match_checks_every_field_the_caller_passes() -> None:
    # is_mandatory_opinion_column과 동일하게, 인사 소식이 제목이 아니라 summary에만
    # 있어도 놓치지 않아야 한다.
    target = monitored_personnel_match(
        "장애인 정책 관련 소식",
        None,
        None,
        "국민연금공단 신임 이사장에 OOO 내정",
    )
    assert target is not None
    assert target.label == "국민연금공단 이사장"
