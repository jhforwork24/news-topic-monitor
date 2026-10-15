from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from news_topic_monitor.assurance import CheckStatus, GapDetectionResult
from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.models import (
    BodyStatus,
    Classification,
    EditorialCandidate,
    VerificationStatus,
)
from news_topic_monitor.notion_publish import (
    QUEUE_LEAD_PREVIEW_CHARS,
    READING_TIER_FULL,
    READING_TIER_LEAD,
    EditorialQueueSettings,
    NotionPublisher,
    NotionPublishSettings,
    queue_reading_tier,
    reading_tier_summary,
)
from news_topic_monitor.previous_briefing import load_previous_briefing

WEATHER = (
    "이번 주말은 전국이 대체로 맑고 낮 기온이 크게 오르며 나들이하기 좋은 날씨가 이어지겠다. "
    "다음 주 초부터는 남부 지방을 중심으로 비가 내릴 가능성이 있다. "
) * 15


def _candidate(
    candidate_id: str,
    *,
    source: str,
    title: str = "오늘의 날씨와 주말 나들이 정보",
    evidence: str = WEATHER,
    classification: Classification = Classification.IRRELEVANT,
    byline: str | None = "김기자",
    section: str = "생활",
) -> EditorialCandidate:
    return EditorialCandidate(
        candidate_id=candidate_id,
        source=source,
        canonical_url=f"https://example.com/{candidate_id}",
        title=title,
        byline=byline,
        section=section,
        published_at=datetime(2026, 10, 15, 0, tzinfo=UTC),
        summary=evidence[:200],
        evidence_text=evidence,
        body_status=BodyStatus.FETCHED,
        verification_status=VerificationStatus.BODY_VERIFIED,
        rule_classification=classification,
        rule_score=0.0,
    )


def _labor(topics_path: Path) -> RuleClassifier:
    return RuleClassifier(topics_path, topic="labor_care_poverty")


def test_only_irrelevant_unhinted_mainstream_candidates_are_lead_tier(topics_path: Path) -> None:
    labor = _labor(topics_path)
    assert queue_reading_tier(_candidate("a", source="chosun"), labor) == READING_TIER_LEAD
    # Designated disability / labor / column outlets are always read in full.
    for source in ("ablenews", "beminor", "theindigo", "mediaus", "labortoday", "newscham"):
        assert queue_reading_tier(_candidate(source, source=source), labor) == READING_TIER_FULL
    # A disability-relevant mainstream article is read in full.
    relevant = _candidate("r", source="hani", classification=Classification.RELEVANT)
    assert queue_reading_tier(relevant, labor) == READING_TIER_FULL
    # A labor story the disability rules missed is read in full too.
    strike = _candidate(
        "s",
        source="donga",
        title="현대차 노조, 10년 만에 전면파업 돌입",
        evidence="현대자동차 노동조합이 임금 인상과 정년 연장을 요구하며 전면파업에 돌입했다. " * 4,
        section="경제",
    )
    assert queue_reading_tier(strike, labor) == READING_TIER_FULL
    # A designated fixed-author column stays full whatever the rules say.
    column = _candidate(
        "c",
        source="hani",
        title="[세계의 창] 지제크 칼럼",
        byline="지제크",
    )
    assert queue_reading_tier(column, labor) == READING_TIER_FULL


def test_reading_tier_summary_counts_both_tiers(topics_path: Path) -> None:
    labor = _labor(topics_path)
    candidates = [
        _candidate("a", source="chosun"),
        _candidate("b", source="joongang"),
        _candidate("c", source="ablenews"),
    ]
    assert reading_tier_summary(candidates, labor) == (1, 2)


def _publisher(requests: list) -> NotionPublisher:
    created = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        if request.method == "POST" and request.url.path == "/v1/pages":
            created += 1
            return httpx.Response(200, json={"id": f"page-{created}", "url": "https://notion.so/p"})
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    return NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="reports-ds"), client=client
    )


def test_queue_renders_lead_preview_but_keeps_full_machine_evidence(topics_path: Path) -> None:
    requests: list = []
    publisher = _publisher(requests)
    lead = _candidate("lead-one", source="chosun")
    full = _candidate("full-one", source="ablenews")
    previous = load_previous_briefing(Path("/nonexistent"), "2026-10-15")
    assert previous is None

    publisher.publish_editorial_queue(
        [lead, full],
        report_date="2026-10-15",
        start=datetime(2026, 10, 14, 0, tzinfo=UTC),
        end=datetime(2026, 10, 15, 0, tzinfo=UTC),
        initial_health_finished_at=datetime(2026, 10, 15, 0, tzinfo=UTC),
        gap_detection=GapDetectionResult(
            status=CheckStatus.COMPLETE,
            route="naver_api_hub",
            queries_attempted=5,
            queries_completed=5,
        ),
        queue_settings=EditorialQueueSettings(
            max_candidates=20, chunk_size=20, evidence_chars=1000
        ),
        labor_classifier=_labor(topics_path),
    )

    creates = [body for method, path, body in requests if method == "POST" and path == "/v1/pages"]
    rendered = json.dumps(creates, ensure_ascii=False)
    assert f"읽기=앞부분({QUEUE_LEAD_PREVIEW_CHARS}자)" in rendered
    assert "확인 근거(앞부분): " in rendered
    assert "읽기 분류: 전문 1개 · 앞부분 1개" in rendered
    # The machine JSON still carries the whole bounded evidence for the lead candidate.
    part = next(
        body
        for body in creates
        if "후보 묶음" in json.dumps(body["children"], ensure_ascii=False)
        and "기계 판독용 입력" in json.dumps(body["children"], ensure_ascii=False)
    )
    code = next(block for block in part["children"] if block["type"] == "code")
    machine = json.loads("".join(item["text"]["content"] for item in code["code"]["rich_text"]))
    lead_machine = next(
        item for item in machine["candidates"] if item["candidate_id"] == "lead-one"
    )
    assert len(lead_machine["evidence_text"]) == 1000
    # The readable paragraph for the lead candidate is cut to the preview length.
    paragraphs = [
        "".join(item["text"]["content"] for item in block["paragraph"]["rich_text"])
        for block in part["children"]
        if block["type"] == "paragraph"
    ]
    preview = next(text for text in paragraphs if text.startswith("확인 근거(앞부분): "))
    assert len(preview) <= len("확인 근거(앞부분): ") + QUEUE_LEAD_PREVIEW_CHARS + 2


def test_previous_briefing_titles_come_from_the_latest_earlier_file(tmp_path: Path) -> None:
    folder = tmp_path / "reports" / "briefings"
    folder.mkdir(parents=True)
    (folder / "2026-10-13.md").write_text("# x\n\n## 1. 오래된 이슈\n", encoding="utf-8")
    (folder / "2026-10-14.md").write_text(
        "# 브리핑\n\n# I. 장애\n\n## 1. 첫 이슈 제목\n\n### 소제목\n\n## 2. 둘째 이슈 제목\n",
        encoding="utf-8",
    )
    (folder / "2026-10-15.md").write_text("## 1. 오늘 이슈\n", encoding="utf-8")
    (folder / "notes.txt").write_text("무시", encoding="utf-8")

    previous = load_previous_briefing(tmp_path, "2026-10-15")

    assert previous is not None
    assert previous.report_date == "2026-10-14"
    assert previous.issue_titles == ("첫 이슈 제목", "둘째 이슈 제목")
    assert load_previous_briefing(tmp_path, "2026-10-13") is None


def test_queue_manifest_lists_previous_issue_titles_without_touching_machine_json() -> None:
    from news_topic_monitor.chat_bridge import ChatEditorialQueueManifest
    from news_topic_monitor.notion_publish import _queue_manifest_blocks
    from news_topic_monitor.previous_briefing import PreviousBriefing

    manifest = ChatEditorialQueueManifest(
        report_date="2026-10-15",
        queue_id="a" * 64,
        generated_at=datetime(2026, 10, 14, 22, tzinfo=UTC),
        report_start=datetime(2026, 10, 13, 20, tzinfo=UTC),
        report_end=datetime(2026, 10, 14, 20, tzinfo=UTC),
        initial_health_finished_at=datetime(2026, 10, 14, 21, tzinfo=UTC),
        candidate_count=1,
        part_count=1,
        gap_detection_status="complete",
        gap_detection_route="naver_api_hub",
        gap_queries_attempted=5,
        gap_queries_completed=5,
        gap_potential_count=0,
    )
    blocks = _queue_manifest_blocks(
        manifest=manifest,
        part_pages=[],
        source_failures=[],
        previous_briefing=PreviousBriefing("2026-10-14", ("첫 이슈 제목",)),
    )
    texts = [
        "".join(item["text"]["content"] for item in block[block["type"]]["rich_text"])
        for block in blocks
        if block["type"] in {"paragraph", "heading_2", "bulleted_list_item"}
    ]
    assert "직전 발행 브리핑 이슈 (2026-10-14, 참고)" in texts
    assert "첫 이슈 제목" in texts
    code = next(block for block in blocks if block["type"] == "code")
    payload = json.loads("".join(item["text"]["content"] for item in code["code"]["rich_text"]))
    assert "previous_briefing" not in payload
