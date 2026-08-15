from __future__ import annotations

from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.models import Classification


def test_disability_rights_article_is_relevant(topics_path) -> None:
    result = RuleClassifier(topics_path).classify(
        title="중증장애인 활동지원 확대와 이동권 보장 촉구",
        summary="장애인권 단체가 지역사회 자립 정책을 요구했다.",
    )
    assert result.classification == Classification.RELEVANT
    assert "중증장애인" in result.matched_terms
    assert result.topic_score >= 8


def test_bare_ambiguous_term_is_not_enough(topics_path) -> None:
    result = RuleClassifier(topics_path).classify(title="협상의 장애 요인 세 가지")
    assert result.classification == Classification.IRRELEVANT


def test_server_and_telecom_outages_are_excluded(topics_path) -> None:
    classifier = RuleClassifier(topics_path)
    for title in ("서버 장애로 서비스 중단", "통신 장애 전국 확산", "시스템 장애 복구"):
        result = classifier.classify(title=title)
        assert result.classification == Classification.IRRELEVANT
        assert result.excluded_terms
        assert not result.candidate


def test_exclusion_does_not_erase_strong_human_context(topics_path) -> None:
    result = RuleClassifier(topics_path).classify(
        title="장애인 활동지원 신청 시스템 장애", summary="장애인 이용자 권리 침해가 이어졌다."
    )
    assert result.classification == Classification.RELEVANT
    assert "시스템 장애" in result.excluded_terms


def test_review_boundary(topics_path) -> None:
    result = RuleClassifier(topics_path).classify(title="지역사회 이동권 논의")
    assert result.classification == Classification.REVIEW
    assert result.candidate


def test_irrelevant_boundary(topics_path) -> None:
    result = RuleClassifier(topics_path).classify(title="오늘의 날씨와 주말 나들이")
    assert result.classification == Classification.IRRELEVANT
    assert result.topic_score == 0


def test_labor_care_poverty_topic_is_independently_configured(topics_path) -> None:
    result = RuleClassifier(topics_path, topic="labor_care_poverty").classify(
        title="공공돌봄 노동자 임금과 고용 보장 촉구",
        summary="돌봄노동자의 노동권을 보장하라는 요구가 제기됐다.",
    )
    assert result.classification == Classification.RELEVANT
    assert result.topic == "labor_care_poverty"


def test_labor_topic_excludes_north_korean_party_usage(topics_path) -> None:
    result = RuleClassifier(topics_path, topic="labor_care_poverty").classify(
        title="조선노동당 기관지 노동신문 보도"
    )
    assert result.classification == Classification.IRRELEVANT
    assert result.excluded_terms
