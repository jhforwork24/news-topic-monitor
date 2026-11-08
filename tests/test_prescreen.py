from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from news_topic_monitor import cli
from news_topic_monitor.chat_bridge import bounded_queue_candidate, editorial_queue_id
from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.editorial import select_chat_editorial_candidates
from news_topic_monitor.models import (
    BodyStatus,
    Classification,
    EditorialCandidate,
    VerificationStatus,
)
from news_topic_monitor.notion_publish import EditorialQueueSettings
from news_topic_monitor.prescreen import (
    PRESCREEN_MODE_OFF,
    PRESCREEN_MODE_SHADOW,
    compare_shadow,
    decide,
    prescreen_mode,
    recall_clues,
    shadow_record,
    shadow_record_path,
    write_shadow_record,
)

ROOT = Path(__file__).resolve().parents[1]
TOPICS = ROOT / "config" / "topics.yml"
WEATHER = (
    "이번 주말은 전국이 대체로 맑고 낮 기온이 크게 오르며 나들이하기 좋은 날씨가 이어지겠다. "
    "다음 주 초부터는 남부 지방을 중심으로 비가 내릴 가능성이 있다. "
) * 15


@pytest.fixture(scope="module")
def labor_classifier() -> RuleClassifier:
    return RuleClassifier(TOPICS, topic="labor_care_poverty")


@pytest.fixture(scope="module")
def clues() -> tuple[str, ...]:
    return recall_clues(TOPICS)


def _candidate(
    candidate_id: str,
    *,
    source: str = "donga",
    title: str = "오늘의 날씨와 주말 나들이 정보",
    evidence: str = WEATHER,
    classification: Classification = Classification.IRRELEVANT,
    score: float = 0.0,
    section: str = "생활",
    byline: str | None = "김기자",
    body_status: BodyStatus = BodyStatus.FETCHED,
    verification: VerificationStatus = VerificationStatus.BODY_VERIFIED,
) -> EditorialCandidate:
    return EditorialCandidate(
        candidate_id=candidate_id,
        source=source,
        canonical_url=f"https://example.com/{candidate_id}",
        title=title,
        byline=byline,
        section=section,
        published_at=datetime(2026, 11, 10, 0, tzinfo=UTC),
        summary=evidence[:200],
        evidence_text=evidence,
        body_status=body_status,
        verification_status=verification,
        rule_classification=classification,
        rule_score=score,
    )


def test_plain_unrelated_mainstream_story_would_be_excluded(labor_classifier, clues) -> None:
    decision = decide(_candidate("a" * 64), labor_classifier=labor_classifier, clues=clues)
    assert decision.would_exclude is True


@pytest.mark.parametrize("source", ["beminor", "ablenews", "theindigo", "labortoday", "newscham"])
def test_designated_and_specialist_sources_always_stay(source, labor_classifier, clues) -> None:
    decision = decide(
        _candidate("b" * 64, source=source), labor_classifier=labor_classifier, clues=clues
    )
    assert decision.would_exclude is False
    assert decision.reason == "keep:designated_or_specialist_source"


def test_positive_rule_score_stays(labor_classifier, clues) -> None:
    decision = decide(
        _candidate("c" * 64, score=0.5), labor_classifier=labor_classifier, clues=clues
    )
    assert decision.would_exclude is False


def test_unverified_body_stays(labor_classifier, clues) -> None:
    decision = decide(
        _candidate(
            "d" * 64,
            body_status=BodyStatus.NOT_REQUESTED,
            verification=VerificationStatus.METADATA_ONLY,
        ),
        labor_classifier=labor_classifier,
        clues=clues,
    )
    assert decision.would_exclude is False
    assert decision.reason == "keep:evidence_not_verified"


@pytest.mark.parametrize("term", ["성소수자", "난민", "트랜스젠더", "이주노동자", "기초생활보장"])
def test_minority_care_poverty_clue_deep_in_body_keeps_story(term, labor_classifier, clues) -> None:
    # The clue sits far past the 300-char lead the editor sees for lead-tier
    # candidates, so only the whole-evidence scan can catch it.
    evidence = WEATHER + f" 한편 행사장에는 {term} 관련 단체도 참석했다."
    decision = decide(
        _candidate("e" * 64, evidence=evidence), labor_classifier=labor_classifier, clues=clues
    )
    assert decision.would_exclude is False


def test_clue_set_tracks_topic_and_subsection_terms(clues) -> None:
    expected = ("전장연", "장애인차별금지법", "성소수자", "트랜스젠더", "난민", "자활근로", "간병")
    for term in expected:
        assert term in clues
    # Negative context terms are not recall clues.
    assert "서버 장애" not in clues


def test_fixed_column_stays_even_without_topic_signal(labor_classifier, clues) -> None:
    decision = decide(
        _candidate(
            "f" * 64,
            source="khan",
            title="[고병권의 묵묵] 겨울 산책",
            section="오피니언",
            byline="고병권",
        ),
        labor_classifier=labor_classifier,
        clues=clues,
    )
    assert decision.would_exclude is False


def test_mode_parsing() -> None:
    assert prescreen_mode({}) == (PRESCREEN_MODE_SHADOW, None)
    assert prescreen_mode({"QUEUE_PRESCREEN_MODE": " OFF "}) == (PRESCREEN_MODE_OFF, None)
    assert prescreen_mode({"QUEUE_PRESCREEN_MODE": "shadow"}) == (PRESCREEN_MODE_SHADOW, None)
    # Apply mode does not exist yet: it falls back to shadow and is recorded.
    mode, note = prescreen_mode({"QUEUE_PRESCREEN_MODE": "apply"})
    assert mode == PRESCREEN_MODE_SHADOW
    assert note == "unsupported_mode:apply"


def test_shadow_record_holds_no_article_text(labor_classifier) -> None:
    bounded = [_candidate("1" * 64), _candidate("2" * 64, source="beminor")]
    record = shadow_record(
        bounded,
        report_date="2026-11-10",
        queue_id="q" * 64,
        labor_classifier=labor_classifier,
        topics_path=TOPICS,
    )
    serialized = json.dumps(record, ensure_ascii=False, default=str)
    assert record["applied"] is False
    assert record["candidate_count"] == 2
    assert record["would_exclude"] == [{"candidate_id": "1" * 64, "source": "donga"}]
    assert "날씨" not in serialized
    assert "example.com" not in serialized


def test_cli_shadow_is_bound_to_queue_and_leaves_candidates_untouched(
    tmp_path, labor_classifier, monkeypatch
) -> None:
    monkeypatch.delenv("QUEUE_PRESCREEN_MODE", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yml").write_text(TOPICS.read_text(encoding="utf-8"))
    candidates = [_candidate(f"{i:064x}") for i in range(3)]
    before = [candidate.model_dump() for candidate in candidates]
    settings = EditorialQueueSettings()
    queue_id = editorial_queue_id(
        [
            bounded_queue_candidate(candidate, settings.evidence_chars)
            for candidate in select_chat_editorial_candidates(candidates, settings.max_candidates)
        ]
    )

    record = cli._prescreen_shadow(
        tmp_path,
        candidates,
        report_date="2026-11-10",
        queue_settings=settings,
        labor_classifier=labor_classifier,
        expected_queue_id=queue_id,
    )

    assert record is not None
    assert record["queue_id"] == queue_id
    assert record["would_exclude_count"] == 3
    assert [candidate.model_dump() for candidate in candidates] == before


def test_cli_shadow_records_mismatch_and_errors_without_raising(
    tmp_path, labor_classifier, monkeypatch
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "topics.yml").write_text(TOPICS.read_text(encoding="utf-8"))
    settings = EditorialQueueSettings()
    candidates = [_candidate("3" * 64)]
    mismatch = cli._prescreen_shadow(
        tmp_path,
        candidates,
        report_date="2026-11-10",
        queue_settings=settings,
        labor_classifier=labor_classifier,
        expected_queue_id="0" * 64,
    )
    assert mismatch is not None and mismatch["error"] == "queue_id_mismatch"

    def boom(*args, **kwargs):
        raise RuntimeError("broken clue file")

    monkeypatch.setattr(cli, "shadow_record", boom)
    failed = cli._prescreen_shadow(
        tmp_path,
        candidates,
        report_date="2026-11-10",
        queue_settings=settings,
        labor_classifier=labor_classifier,
        expected_queue_id=None,
    )
    assert failed is not None and "RuntimeError" in failed["error"]


def test_cli_shadow_off_records_nothing(tmp_path, labor_classifier, monkeypatch) -> None:
    monkeypatch.setenv("QUEUE_PRESCREEN_MODE", "off")
    assert (
        cli._prescreen_shadow(
            tmp_path,
            [_candidate("4" * 64)],
            report_date="2026-11-10",
            queue_settings=EditorialQueueSettings(),
            labor_classifier=labor_classifier,
            expected_queue_id=None,
        )
        is None
    )


def _write_shadow(root: Path, excluded: list[str], queue_id: str = "q" * 64) -> None:
    write_shadow_record(
        root,
        {
            "report_date": "2026-11-10",
            "queue_id": queue_id,
            "would_exclude_count": len(excluded),
            "would_exclude": [{"candidate_id": cid, "source": "donga"} for cid in excluded],
        },
    )


def _write_evidence(root: Path, published: dict[str, str | None]) -> None:
    path = root / "evidence" / "2026-11-10.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "articles": [
                    {"candidate_id": cid, "issue_id": issue, "title": f"제목 {cid[:4]}"}
                    for cid, issue in published.items()
                ]
            }
        ),
        encoding="utf-8",
    )


def test_compare_statuses(tmp_path) -> None:
    assert compare_shadow(tmp_path, "2026-11-10")["status"] == "no_shadow_record"

    _write_shadow(tmp_path, ["a" * 64])
    assert shadow_record_path(tmp_path, "2026-11-10").exists()
    assert compare_shadow(tmp_path, "2026-11-10")["status"] == "no_published_evidence"

    _write_evidence(tmp_path, {"a" * 64: None, "b" * 64: "2026-11-10-issue-01"})
    result = compare_shadow(tmp_path, "2026-11-10")
    assert result["status"] == "no_overlap"
    assert result["published_candidate_count"] == 1

    _write_evidence(tmp_path, {"a" * 64: "2026-11-10-issue-02"})
    miss = compare_shadow(tmp_path, "2026-11-10")
    assert miss["status"] == "recall_miss"
    assert miss["overlap"][0]["issue_id"] == "2026-11-10-issue-02"


def test_compare_refuses_shadow_from_another_queue(tmp_path) -> None:
    _write_shadow(tmp_path, ["a" * 64], queue_id="q" * 64)
    bound = tmp_path / "health" / "editorial_queue" / "initial_health" / "2026-11-10.json"
    bound.parent.mkdir(parents=True, exist_ok=True)
    bound.write_text(json.dumps({"queue_id": "r" * 64}), encoding="utf-8")
    assert compare_shadow(tmp_path, "2026-11-10")["status"] == "queue_mismatch"


def test_compare_script_exit_codes(tmp_path, monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    sys.modules.pop("prescreen_shadow_compare", None)
    import prescreen_shadow_compare

    _write_shadow(tmp_path, ["a" * 64])
    _write_evidence(tmp_path, {"a" * 64: None})
    assert prescreen_shadow_compare.main(["--root", str(tmp_path), "--all", "--write"]) == 0
    written = tmp_path / "health" / "editorial_queue" / "prescreen_shadow" / "compare"
    assert (written / "2026-11-10.json").exists()

    _write_evidence(tmp_path, {"a" * 64: "2026-11-10-issue-01"})
    assert prescreen_shadow_compare.main(["--root", str(tmp_path), "--date", "2026-11-10"]) == 1
