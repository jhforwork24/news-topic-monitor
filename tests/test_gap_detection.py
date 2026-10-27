from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from news_topic_monitor.assurance import CheckStatus
from news_topic_monitor.gap_detection import (
    NaverSearchClient,
    NaverSearchSettings,
    known_title_keys,
    run_gap_detection,
    run_reverse_search,
)
from news_topic_monitor.models import EditorialIssueDecision, EditorialPlan, EditorialSection
from news_topic_monitor.policy import load_briefing_policy, load_source_registry


def _client(requests: list[httpx.Request]) -> NaverSearchClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "<b>장애인</b> 이동권 보도",
                        "originallink": "https://www.hani.co.kr/arti/society/1.html",
                        "link": "https://n.news.naver.com/article/028/1",
                        "description": "장애인 이동권과 국가 책임을 다룬 기사",
                        "pubDate": "Thu, 20 Aug 2026 08:30:00 +0900",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    settings = NaverSearchSettings(client_id="id", client_secret="secret", max_retries=0)
    return NaverSearchClient(settings, client=http_client)


def test_naver_api_hub_headers_and_gap_result_are_discovery_only() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    registry = load_source_registry(root / "config" / "source-registry.yaml")
    requests: list[httpx.Request] = []
    client = _client(requests)
    end = datetime(2026, 8, 20, 1, tzinfo=UTC)

    result = run_gap_detection(
        client=client,
        configuration_error=None,
        policy=policy,
        registry=registry,
        known_canonical_urls=set(),
        start=end - timedelta(days=1),
        end=end,
    )

    assert result.status == CheckStatus.COMPLETE
    assert result.hits[0].matched_source == "hani"
    assert result.hits[0].in_deterministic_collection is False
    assert result.potential_gaps == result.hits[:1]
    assert result.hits[0].title == "장애인 이동권 보도"
    assert requests[0].headers["X-NCP-APIGW-API-KEY-ID"] == "id"
    assert requests[0].headers["X-NCP-APIGW-API-KEY"] == "secret"
    assert json.loads(requests[0].url.params.get("display")) == 100

    collected = run_gap_detection(
        client=client,
        configuration_error=None,
        policy=policy,
        registry=registry,
        known_canonical_urls={"https://www.hani.co.kr/arti/society/1.html"},
        start=end - timedelta(days=1),
        end=end,
    )
    assert collected.hits[0].in_deterministic_collection is True
    assert collected.potential_gaps == []


def _gap(known_urls: set[str], titles: dict[str, set[str]] | None = None):
    root = __import__("pathlib").Path(__file__).parents[1]
    end = datetime(2026, 8, 20, 1, tzinfo=UTC)
    return run_gap_detection(
        client=_client([]),
        configuration_error=None,
        policy=load_briefing_policy(root / "config" / "briefing-policy.yaml"),
        registry=load_source_registry(root / "config" / "source-registry.yaml"),
        known_canonical_urls=known_urls,
        known_titles=titles,
        start=end - timedelta(days=1),
        end=end,
    )


def test_search_hit_matches_stored_article_despite_scheme_and_www_difference() -> None:
    # The collector stores https://; Naver may return the same article as http:// or without www.
    result = _gap({"http://hani.co.kr/arti/society/1.html"})
    assert result.hits[0].in_deterministic_collection is True
    assert result.potential_gaps == []


def test_search_hit_matches_stored_article_by_source_and_title() -> None:
    class Stored:
        source = "hani"
        title = "장애인  이동권 보도"

    # URL differs (page suffix / query), but the same source already holds this title.
    result = _gap(
        {"https://www.hani.co.kr/arti/society/1_other.html"}, known_title_keys([Stored()])
    )
    assert result.hits[0].in_deterministic_collection is True

    class OtherSource:
        source = "donga"
        title = "장애인 이동권 보도"

    # The same title under another source is not a match.
    result = _gap(set(), known_title_keys([OtherSource()]))
    assert result.hits[0].in_deterministic_collection is False
    assert len(result.potential_gaps) == 1


def test_reverse_search_records_explicit_degraded_10_of_10_without_credentials() -> None:
    root = __import__("pathlib").Path(__file__).parents[1]
    policy = load_briefing_policy(root / "config" / "briefing-policy.yaml")
    registry = load_source_registry(root / "config" / "source-registry.yaml")
    plan = EditorialPlan(
        issues=[
            EditorialIssueDecision(
                section=EditorialSection.DISABILITY,
                title="장애인 이동권 예산",
                keyword="장애인 이동권 예산",
                candidate_ids=["candidate"],
                summary="장애인 이동권 예산의 국가 책임을 다룬다.",
                tone_analysis="",
            )
        ],
        exclusions=[],
    )
    end = datetime(2026, 8, 20, 1, tzinfo=UTC)
    result = run_reverse_search(
        client=None,
        configuration_error="credentials missing",
        plan=plan,
        policy=policy,
        registry=registry,
        start=end - timedelta(days=1),
        end=end,
    )
    assert result.status == CheckStatus.DEGRADED
    assert len(result.checks) == 10
    assert all(check.status == CheckStatus.DEGRADED for check in result.checks)
