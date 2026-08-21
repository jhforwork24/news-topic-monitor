from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime

import httpx
import pytest

from news_topic_monitor.assurance import CheckStatus, GapDetectionResult
from news_topic_monitor.chat_bridge import editorial_queue_payload_id
from news_topic_monitor.editorial import select_chat_editorial_candidates
from news_topic_monitor.models import (
    BodyStatus,
    Classification,
    EditorialCandidate,
    VerificationStatus,
)
from news_topic_monitor.notion_publish import (
    EditorialQueueSettings,
    NotionApiError,
    NotionPublisher,
    NotionPublishSettings,
    write_editorial_queue_health,
)


def _candidate(
    candidate_id: str,
    *,
    source: str = "hani",
    title: str = "장애인 이동권 보장을 요구한 기자회견",
    section: str = "사회",
    published_at: datetime | None = datetime(2026, 8, 17, 0, tzinfo=UTC),
    verification_status: VerificationStatus = VerificationStatus.BODY_VERIFIED,
) -> EditorialCandidate:
    evidence = (
        "장애인단체는 이동권 보장을 위해 저상버스와 특별교통수단 예산을 확대하고 "
        "지방정부가 법정 기준을 이행해야 한다고 요구했다. 담당 기관은 예산과 운행 계획을 "
        "검토하겠다고 밝혔으나 구체적인 이행 시점은 제시하지 않았다."
    )
    return EditorialCandidate(
        candidate_id=candidate_id,
        source=source,
        canonical_url=f"https://example.com/{candidate_id}",
        title=title,
        byline="김기자",
        section=section,
        published_at=published_at,
        summary=evidence,
        evidence_text=evidence,
        body_status=(
            BodyStatus.FETCHED
            if verification_status == VerificationStatus.BODY_VERIFIED
            else BodyStatus.NOT_REQUESTED
        ),
        verification_status=verification_status,
        rule_classification=Classification.RELEVANT,
        rule_score=10.0,
    )


def test_chat_queue_requires_exact_date_and_verified_evidence() -> None:
    verified = _candidate("verified")
    missing_date = _candidate("missing-date", published_at=None)
    metadata_only = _candidate(
        "metadata-only", verification_status=VerificationStatus.METADATA_ONLY
    )
    broadcast = _candidate(
        "broadcast",
        source="mbc",
        verification_status=VerificationStatus.METADATA_ONLY,
    )

    selected = select_chat_editorial_candidates(
        [verified, missing_date, metadata_only, broadcast], 20
    )

    assert {item.candidate_id for item in selected} == {"verified", "broadcast"}


def test_notion_queue_trashes_old_pages_and_creates_manifest_without_kst() -> None:
    requests: list[tuple[str, str, dict]] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "old-queue"}],
                    "has_more": False,
                },
            )
        if request.method == "PATCH" and request.url.path == "/v1/pages/old-queue":
            return httpx.Response(200, json={"id": "old-queue", "in_trash": True})
        if request.method == "POST" and request.url.path == "/v1/pages":
            create_count += 1
            return httpx.Response(
                200,
                json={
                    "id": f"page-{create_count}",
                    "url": f"https://notion.so/page-{create_count}",
                },
            )
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="reports-ds"),
        client=client,
    )
    result = publisher.publish_editorial_queue(
        [
            _candidate("normal"),
            _candidate(
                "photo",
                title="[포토뉴스] 노동자 집회 현장",
                section="포토",
            ),
        ],
        report_date="2026-08-17",
        start=datetime(2026, 8, 16, 0, tzinfo=UTC),
        end=datetime(2026, 8, 17, 0, tzinfo=UTC),
        initial_health_finished_at=datetime(2026, 8, 17, 0, tzinfo=UTC),
        gap_detection=GapDetectionResult(
            status=CheckStatus.COMPLETE,
            route="naver_api_hub",
            queries_attempted=5,
            queries_completed=5,
        ),
        queue_settings=EditorialQueueSettings(
            max_candidates=20,
            chunk_size=2,
            evidence_chars=1000,
        ),
        source_failures=["sbs: robots.txt 확인 실패"],
    )

    assert result.status == "ready"
    assert result.candidate_count == 2
    assert result.part_count == 1
    assert len(result.queue_id) == 64
    assert result.gap_detection_status == "complete"
    assert json.loads(json.dumps(result.log_payload()))["generated_at"].endswith("+00:00")
    assert any(
        method == "PATCH" and path == "/v1/pages/old-queue" and body == {"in_trash": True}
        for method, path, body in requests
    )
    archive_index = next(
        index
        for index, (method, path, _body) in enumerate(requests)
        if method == "PATCH" and path == "/v1/pages/old-queue"
    )
    create_indexes = [
        index
        for index, (method, path, _body) in enumerate(requests)
        if method == "POST" and path == "/v1/pages"
    ]
    assert archive_index > max(create_indexes)
    creates = [body for method, path, body in requests if method == "POST" and path == "/v1/pages"]
    assert len(creates) == 2
    rendered = json.dumps(creates, ensure_ascii=False)
    assert "II절 제외: 사진·화보 중심 보도" in rendered
    assert "상태=READY" in rendered
    assert "후보 묶음 01" in rendered
    assert "KST" not in rendered
    code_documents = []
    for created in creates:
        for block in created["children"]:
            if block.get("type") != "code":
                continue
            chunks = [item["text"]["content"] for item in block["code"]["rich_text"]]
            assert all(not any(character.isspace() for character in chunk) for chunk in chunks)
            assert all(chunk.isascii() for chunk in chunks)
            content = "".join(chunks)
            assert unicodedata.normalize("NFC", content) == content
            code_documents.append(json.loads(content))
    assert [document["schema_version"] for document in code_documents] == [1, 1]
    part_document = next(document for document in code_documents if "candidates" in document)
    assert editorial_queue_payload_id(part_document["candidates"]) == result.queue_id
    assert result.queue_id in rendered


def test_queue_partitions_ascii_machine_json_below_notion_rich_text_limit() -> None:
    requests: list[tuple[str, str, dict]] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={"results": [{"id": "old-queue"}], "has_more": False},
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})
        if request.method == "POST" and request.url.path == "/v1/pages":
            create_count += 1
            return httpx.Response(
                200,
                json={
                    "id": f"page-{create_count}",
                    "url": f"https://notion.so/page-{create_count}",
                },
            )
        return httpx.Response(404, json={"message": "unexpected"})

    candidates = [
        _candidate(f"candidate-{index:02d}").model_copy(
            update={"summary": "가" * 1800, "evidence_text": "나" * 1800}
        )
        for index in range(24)
    ]
    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="reports-ds"),
        client=client,
    )

    result = publisher.publish_editorial_queue(
        candidates,
        report_date="2026-08-17",
        start=datetime(2026, 8, 16, 0, tzinfo=UTC),
        end=datetime(2026, 8, 17, 0, tzinfo=UTC),
        initial_health_finished_at=datetime(2026, 8, 17, 0, tzinfo=UTC),
        gap_detection=GapDetectionResult(
            status=CheckStatus.COMPLETE,
            route="naver_api_hub",
            queries_attempted=5,
            queries_completed=5,
        ),
        queue_settings=EditorialQueueSettings(
            max_candidates=24,
            chunk_size=24,
            evidence_chars=1800,
        ),
    )

    documents: list[dict] = []
    for method, path, body in requests:
        if method != "POST" or path != "/v1/pages":
            continue
        for block in body["children"]:
            if block.get("type") != "code":
                continue
            rich_text = block["code"]["rich_text"]
            assert len(rich_text) <= 100
            documents.append(json.loads("".join(item["text"]["content"] for item in rich_text)))

    part_documents = [document for document in documents if "candidates" in document]
    assert result.part_count == len(part_documents)
    assert result.part_count > 1
    assert sum(len(document["candidates"]) for document in part_documents) == 24


def test_queue_creation_failure_trashes_only_partial_replacement_pages() -> None:
    requests: list[tuple[str, str, dict]] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={"results": [{"id": "old-queue"}], "has_more": False},
            )
        if request.method == "POST" and request.url.path == "/v1/pages":
            create_count += 1
            if create_count == 2:
                return httpx.Response(400, json={"message": "simulated limit"})
            return httpx.Response(200, json={"id": "new-page-1"})
        if request.method == "PATCH" and request.url.path == "/v1/pages/new-page-1":
            return httpx.Response(200, json={"id": "new-page-1", "in_trash": True})
        if request.method == "PATCH" and request.url.path == "/v1/pages/old-queue":
            return httpx.Response(200, json={"id": "old-queue", "in_trash": True})
        return httpx.Response(404, json={"message": "unexpected"})

    candidates = [
        _candidate(f"candidate-{index:02d}").model_copy(
            update={"summary": "가" * 1800, "evidence_text": "나" * 1800}
        )
        for index in range(24)
    ]
    client = httpx.Client(base_url="https://api.notion.com", transport=httpx.MockTransport(handler))
    publisher = NotionPublisher(
        NotionPublishSettings(token="test", data_source_id="reports-ds"),
        client=client,
    )

    with pytest.raises(NotionApiError, match="HTTP 400"):
        publisher.publish_editorial_queue(
            candidates,
            report_date="2026-08-17",
            start=datetime(2026, 8, 16, 0, tzinfo=UTC),
            end=datetime(2026, 8, 17, 0, tzinfo=UTC),
            initial_health_finished_at=datetime(2026, 8, 17, 0, tzinfo=UTC),
            gap_detection=GapDetectionResult(
                status=CheckStatus.COMPLETE,
                route="naver_api_hub",
                queries_attempted=5,
                queries_completed=5,
            ),
            queue_settings=EditorialQueueSettings(
                max_candidates=24,
                chunk_size=24,
                evidence_chars=1800,
            ),
        )

    assert any(
        method == "PATCH" and path == "/v1/pages/new-page-1" for method, path, _body in requests
    )
    assert not any(
        method == "PATCH" and path == "/v1/pages/old-queue" for method, path, _body in requests
    )


def test_queue_health_never_records_private_page_url(tmp_path) -> None:
    write_editorial_queue_health(
        tmp_path,
        report_date="2026-08-17",
        status="ready",
        candidate_count=20,
        part_count=1,
        gap_detection_status="complete",
        gap_potential_count=0,
    )
    payload = json.loads(
        (tmp_path / "health" / "editorial_queue" / "latest.json").read_text(encoding="utf-8")
    )
    assert payload["candidate_count"] == 20
    assert payload["gap_detection_status"] == "complete"
    assert "page_url" not in payload
    assert "notion" not in json.dumps(payload).lower()
