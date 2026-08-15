from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import ArticleRecord, Classification
from .sources import SOURCE_LABELS
from .storage import JsonlStorage
from .utils import kst_display


def generate_report(
    storage: JsonlStorage,
    *,
    start: datetime,
    end: datetime,
    report_date: str,
    output_path: Path | None = None,
) -> Path:
    articles = [
        article
        for article in storage.iter_articles()
        if start <= (article.published_at or article.first_seen_at) < end
    ]
    articles.sort(key=lambda article: article.published_at or article.first_seen_at)
    counts = Counter(article.classification.value for article in articles)
    source_counts = Counter(article.source for article in articles)
    health = _load_health(storage.health_path)
    path = output_path or storage.root / "reports" / f"{report_date}.md"
    lines = [
        f"# 장애인권 뉴스 모니터링 일일보고 — {report_date}",
        "",
        f"- 조사기간: {kst_display(start)} 이상, {kst_display(end)} 미만",
        f"- 전체 기사 메타데이터: {len(articles)}건",
        f"- 관련 기사: {counts['relevant']}건",
        f"- 검토 필요: {counts['review']}건",
        f"- 비관련: {counts['irrelevant']}건",
        f"- 발행시각 미상(최초 발견시각으로 구간 배정): "
        f"{sum(article.published_at is None for article in articles)}건",
        "- 원문 보관: 하지 않음(본문은 판별 직후 폐기하고 해시·판정만 저장)",
        "",
        "## 출처별 수집 현황",
        "",
        "| 출처 | 기간 내 저장 기사 | 최근 실행 상태 |",
        "|---|---:|---|",
    ]
    latest_sources = health.get("sources", {}) if isinstance(health, dict) else {}
    for source in SOURCE_LABELS:
        latest = latest_sources.get(source, {}) if isinstance(latest_sources, dict) else {}
        status = _discovery_status_label(latest)
        errors = latest.get("errors") or []
        if errors:
            status += f" — {str(errors[0])[:120]}"
        lines.append(f"| {SOURCE_LABELS[source]} | {source_counts[source]} | {status} |")

    lines.extend(["", "## 관련 기사", ""])
    relevant = [
        article for article in articles if article.classification == Classification.RELEVANT
    ]
    if relevant:
        lines.extend(_article_lines(relevant))
    else:
        lines.append(
            "기간 내 자동 확정된 관련 기사가 없습니다. "
            "수집 실패를 기사 부재로 해석해서는 안 됩니다."
        )

    lines.extend(["", "## 사람의 검토가 필요한 기사", ""])
    review = [article for article in articles if article.classification == Classification.REVIEW]
    if review:
        lines.extend(_article_lines(review))
    else:
        lines.append("기간 내 검토 대상 기사가 없습니다.")

    partial_failures: list[tuple[str, dict[str, object]]] = []
    for source in SOURCE_LABELS:
        details = latest_sources.get(source) if isinstance(latest_sources, dict) else None
        if not isinstance(details, dict):
            partial_failures.append((source, {"errors": ["아직 실행되지 않음"]}))
        elif not details.get("success") or details.get("errors"):
            partial_failures.append((source, details))
    lines.extend(["", "## 수집 장애 및 해석 유의사항", ""])
    if partial_failures:
        for source, details in partial_failures:
            errors = (
                "; ".join(str(error) for error in details.get("errors", [])) or "상세 오류 없음"
            )
            lines.append(f"- {SOURCE_LABELS.get(source, source)}: {errors}")
    else:
        lines.append("- 최근 실행에서 기록된 출처별 실패가 없습니다.")
    lines.append(
        "- 관련·검토 기사 0건은 수집 성공 상태와 함께 확인해야 하며, "
        "실패 상태에서는 기사 부재를 뜻하지 않습니다."
    )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _article_lines(articles: list[ArticleRecord]) -> list[str]:
    lines: list[str] = []
    for article in articles:
        source = SOURCE_LABELS.get(article.source, article.source)
        lines.extend(
            [
                f"### [{article.title}]({article.canonical_url})",
                "",
                f"- 출처·발행: {source} · {kst_display(article.published_at)}",
                f"- 판정: `{article.classification.value}` / 점수 {article.topic_score:.2f}",
                f"- 판정 근거: {article.classification_reason}",
                f"- 모니터 자체 요약: {article.monitor_summary or '생성되지 않음'}",
                f"- 출처 공개 요약: {article.summary or '제공되지 않음'}",
                f"- 본문 확인 상태: `{article.body_status.value}`",
                "",
            ]
        )
    return lines


def _load_health(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _discovery_status_label(latest: dict[str, object]) -> str:
    status = latest.get("discovery_status")
    labels = {
        "complete": "완전 확인",
        "partial": "부분 확인",
        "configuration_missing": "설정 누락",
        "quota_exceeded": "API 할당량 소진",
        "unavailable": "확인 불능",
        "pending": "미실행",
    }
    if isinstance(status, str) and status in labels:
        return labels[status]
    return "성공" if latest.get("success") else "실패 또는 미확인"
