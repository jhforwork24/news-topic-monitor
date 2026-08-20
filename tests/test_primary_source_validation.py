from news_topic_monitor.models import PrimarySourceValidation
from news_topic_monitor.pipeline import _primary_source_validation


def test_statistical_or_legal_claims_are_marked_pending_for_primary_source_pass() -> None:
    assert (
        _primary_source_validation("정부 자료는 지원 대상이 1,200명이라고 설명했다")
        == PrimarySourceValidation.PENDING
    )
    assert (
        _primary_source_validation("장애인권리보장법 제정 요구를 보도했다")
        == PrimarySourceValidation.PENDING
    )
    assert (
        _primary_source_validation("당사자들이 지역사회에서 살아갈 권리를 요구했다")
        == PrimarySourceValidation.NOT_REQUIRED
    )
