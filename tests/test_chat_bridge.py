from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from news_topic_monitor.chat_bridge import (
    ChatEditorialAuditSubmission,
    ChatEditorialBridgeBundle,
    ChatEditorialDraft,
    ChatEditorialQueue,
    ChatEditorialQueueManifest,
    ChatEditorialQueuePart,
    bounded_queue_candidate,
    editorial_queue_id,
    editorial_queue_payload_id,
    validate_chat_editorial_bridge,
)
from news_topic_monitor.cli import _load_bound_initial_health
from news_topic_monitor.editorial import EditorialValidationError
from news_topic_monitor.models import (
    BodyStatus,
    Classification,
    EditorialAudit,
    EditorialCandidate,
    EditorialIssueDecision,
    EditorialPlan,
    EditorialSection,
    RunHealth,
    VerificationStatus,
)
from news_topic_monitor.notion_publish import (
    EditorialQueueValidationError,
    NotionPublisher,
    NotionPublishSettings,
)
from news_topic_monitor.storage import JsonlStorage


def _candidate(candidate_id: str = "candidate-1") -> EditorialCandidate:
    evidence = "장애인 노동권 보장과 공공의 책임을 확인한 공식 원문 근거입니다. " * 6
    return EditorialCandidate(
        candidate_id=candidate_id,
        source="hani",
        canonical_url=f"https://example.com/{candidate_id}",
        title="권리중심공공일자리 노동자들이 고용승계를 요구했다",
        byline="김기자",
        section="사회",
        published_at=datetime(2026, 8, 21, 0, 10, tzinfo=UTC),
        summary=evidence,
        evidence_text=evidence,
        body_status=BodyStatus.FETCHED,
        verification_status=VerificationStatus.BODY_VERIFIED,
        rule_classification=Classification.RELEVANT,
        rule_score=10,
    )


def _bundle() -> ChatEditorialBridgeBundle:
    candidate = _candidate()
    queue_id = editorial_queue_id([candidate])
    generated = datetime(2026, 8, 21, 0, 25, tzinfo=UTC)
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="권리중심공공일자리 고용승계 요구",
                candidate_ids=[candidate.candidate_id],
                summary="장애인 노동자들이 지방정부에 고용승계를 요구했다.",
                tone_analysis="노동권 요구와 지방정부의 책임을 함께 다뤘다.",
            )
        ],
        exclusions=[],
    )
    return ChatEditorialBridgeBundle(
        queue=ChatEditorialQueue(
            manifest=ChatEditorialQueueManifest(
                report_date="2026-08-21",
                queue_id=queue_id,
                generated_at=generated,
                report_start=datetime(2026, 8, 20, 0, tzinfo=UTC),
                report_end=datetime(2026, 8, 21, 0, tzinfo=UTC),
                initial_health_finished_at=generated - timedelta(minutes=1),
                candidate_count=1,
                part_count=1,
                gap_detection_status="complete",
                gap_detection_route="naver_api_hub",
                gap_queries_attempted=5,
                gap_queries_completed=5,
                gap_potential_count=0,
            ),
            candidates=[candidate],
        ),
        draft=ChatEditorialDraft(
            report_date="2026-08-21",
            queue_id=queue_id,
            draft_id="draft-20260821-001",
            submitted_at=generated + timedelta(minutes=5),
            plan=plan,
        ),
        audit=ChatEditorialAuditSubmission(
            report_date="2026-08-21",
            queue_id=queue_id,
            draft_id="draft-20260821-001",
            submitted_at=generated + timedelta(minutes=10),
            audit=EditorialAudit(
                findings=[],
                progressive_issue_titles=[plan.issues[0].title],
            ),
        ),
    )


def test_chat_bridge_validates_bound_queue_draft_and_audit() -> None:
    bundle = _bundle()
    validate_chat_editorial_bridge(bundle)

    mismatched = bundle.model_copy(
        update={"audit": bundle.audit.model_copy(update={"draft_id": "different-draft-id"})}
    )
    with pytest.raises(EditorialValidationError, match="draft_id"):
        validate_chat_editorial_bridge(mismatched)


def test_chat_bridge_rejects_candidate_not_in_verified_queue() -> None:
    bundle = _bundle()
    bad_plan = bundle.draft.plan.model_copy(deep=True)
    bad_plan.issues[0].candidate_ids = ["invented-candidate"]
    bad_bundle = bundle.model_copy(
        update={"draft": bundle.draft.model_copy(update={"plan": bad_plan})}
    )

    with pytest.raises(EditorialValidationError, match="미확인 candidate_id"):
        validate_chat_editorial_bridge(bad_bundle)


def test_notion_bridge_loader_requires_exact_machine_documents() -> None:
    bundle = _bundle()
    part = ChatEditorialQueuePart(
        queue_id=bundle.queue.manifest.queue_id,
        part_index=1,
        part_count=1,
        candidates=bundle.queue.candidates,
    )
    pages = {
        "ChatGPT 편집 대기열 · 2026-08-21 · 매니페스트": "manifest-page",
        "ChatGPT 편집 초안 · 2026-08-21": "draft-page",
        "ChatGPT 독립 감사 · 2026-08-21": "audit-page",
    }
    documents = {
        "manifest-page": bundle.queue.manifest.model_dump(mode="json"),
        "draft-page": bundle.draft.model_dump(mode="json"),
        "audit-page": bundle.audit.model_dump(mode="json"),
        "part-page": part.model_dump(mode="json"),
    }
    documents["part-page"]["candidates"][0]["evidence_text"] += " "
    legacy_queue_id = editorial_queue_payload_id(documents["part-page"]["candidates"])
    for page_id in ("manifest-page", "draft-page", "audit-page", "part-page"):
        documents[page_id]["queue_id"] = legacy_queue_id

    def page(title: str, page_id: str) -> dict:
        return {
            "id": page_id,
            "properties": {
                "이름": {"title": [{"plain_text": title}]},
                "날짜": {"date": {"start": "2026-08-21"}},
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/query"):
            body = json.loads(request.content)
            filters = body.get("filter", {})
            if "and" in filters:
                title = filters["and"][0]["title"]["equals"]
                return httpx.Response(200, json={"results": [page(title, pages[title])]})
            return httpx.Response(
                200,
                json={
                    "results": [page("ChatGPT 편집 대기열 · 2026-08-21 · 01-01", "part-page")],
                    "has_more": False,
                },
            )
        if request.method == "GET" and request.url.path.startswith("/v1/blocks/"):
            page_id = request.url.path.split("/")[3]
            content = json.dumps(
                documents[page_id],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "code",
                            "code": {"rich_text": [{"plain_text": content}]},
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="reports-ds"),
        client=client,
    )

    loaded = publisher.load_chat_editorial_bridge("2026-08-21")
    validate_chat_editorial_bridge(loaded)
    assert loaded.queue.manifest.queue_id == legacy_queue_id
    assert not loaded.queue.candidates[0].evidence_text.endswith(" ")
    assert loaded.draft.plan.issues[0].candidate_ids == ["candidate-1"]

    documents["part-page"]["candidates"][0]["evidence_text"] += "tampered"
    with pytest.raises(EditorialQueueValidationError, match="digest"):
        publisher.load_chat_editorial_bridge("2026-08-21")


def test_bounded_candidate_revalidates_a_whitespace_truncation_boundary() -> None:
    candidate = _candidate().model_copy(update={"evidence_text": "가" * 10 + " " + "나" * 10})

    bounded = bounded_queue_candidate(candidate, 11)
    round_tripped = EditorialCandidate.model_validate(bounded.model_dump(mode="json"))

    assert bounded.evidence_text == "가" * 10
    assert editorial_queue_id([bounded]) == editorial_queue_id([round_tripped])


def test_initial_census_health_is_bound_to_the_queue_manifest(tmp_path) -> None:
    manifest = _bundle().queue.manifest
    health = RunHealth(
        run_started_at=manifest.initial_health_finished_at - timedelta(minutes=5),
        run_finished_at=manifest.initial_health_finished_at,
        window_start=manifest.report_start - timedelta(days=1),
        window_end=manifest.report_end,
        all_sources_failed=False,
        sources={},
    )
    JsonlStorage.atomic_write_json(
        tmp_path / "health" / "latest.json", health.model_dump(mode="json")
    )

    loaded = _load_bound_initial_health(tmp_path, manifest)
    assert loaded.run_finished_at == manifest.initial_health_finished_at

    stale = health.model_copy(
        update={"run_finished_at": manifest.initial_health_finished_at + timedelta(seconds=1)}
    )
    JsonlStorage.atomic_write_json(
        tmp_path / "health" / "latest.json", stale.model_dump(mode="json")
    )
    with pytest.raises(EditorialQueueValidationError, match="일치하지 않음"):
        _load_bound_initial_health(tmp_path, manifest)
