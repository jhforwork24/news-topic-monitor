from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .classifier import RuleClassifier
from .models import ArticleRecord, Classification
from .sources import (
    BROADCAST_SOURCES,
    DISABILITY_PRESS_SOURCES,
    OPINION_FULL_SCAN_SOURCES,
    PRINT_DIGITAL_SOURCES,
    SOURCE_LABELS,
)
from .storage import JsonlStorage
from .utils import kst_display, short_text

OPINION_TERMS = (
    "사설",
    "칼럼",
    "기고",
    "논설",
    "시론",
    "논단",
    "세상읽기",
    "오피니언",
    "횡설수설",
)
OPINION_PATHS = ("/opinion", "/column", "/editorial", "/contribution")
STOPWORDS = {
    "관련",
    "대한",
    "위한",
    "통해",
    "논란",
    "오늘",
    "정부",
    "사회",
    "한국",
    "뉴스",
    "기자",
    "단독",
    "전망",
    "장애",
    "장애인",
    "노동",
    "노동자",
    "노조",
    "정책",
    "지원",
    "촉구",
}
CRPD_TERMS = {
    "제5조 평등과 비차별": ("차별", "평등", "혐오"),
    "제6조 장애여성": ("장애여성", "여성장애"),
    "제7조 장애아동": ("장애아동", "발달장애 아동"),
    "제9조 접근성": ("접근", "편의시설", "정보접근"),
    "제16조 착취·폭력·학대로부터의 자유": ("학대", "폭력", "인권침해"),
    "제19조 자립생활 및 지역사회 통합": ("탈시설", "자립생활", "지역사회"),
    "제20조 개인의 이동": ("이동권", "교통약자", "휠체어"),
    "제24조 교육": ("교육권", "특수교육", "학교"),
    "제25조 건강": ("건강권", "의료접근", "병원"),
    "제27조 근로 및 고용": ("노동", "고용", "일자리", "해고"),
    "제28조 적절한 생활수준과 사회적 보호": ("연금", "소득", "빈곤", "생계"),
    "제29조 정치 및 공적 생활 참여": ("참정권", "선거", "정치참여"),
}


@dataclass
class BriefingIssue:
    title: str
    articles: list[ArticleRecord]
    assessment: str
    references: list[str] = field(default_factory=list)
    crpd_articles: list[str] = field(default_factory=list)


@dataclass
class BriefingSection:
    title: str
    issues: list[BriefingIssue]


@dataclass
class BriefingDocument:
    report_date: str
    start: datetime
    end: datetime
    overview: str
    sections: list[BriefingSection]
    source_failures: list[str]


def build_briefing(
    storage: JsonlStorage,
    *,
    topics_path: Path,
    start: datetime,
    end: datetime,
    report_date: str,
) -> BriefingDocument:
    articles = [
        item
        for item in storage.iter_articles()
        if start <= (item.published_at or item.first_seen_at) < end
    ]
    articles.sort(key=lambda item: item.published_at or item.first_seen_at, reverse=True)
    disability = [
        item
        for item in articles
        if item.source not in BROADCAST_SOURCES
        and item.classification in {Classification.RELEVANT, Classification.REVIEW}
        and not is_opinion(item)
    ]
    disability.sort(key=_disability_priority, reverse=True)
    labor_classifier = RuleClassifier(topics_path, topic="labor_care_poverty")
    labor: list[tuple[ArticleRecord, float]] = []
    for item in articles:
        if item.source in BROADCAST_SOURCES or is_opinion(item):
            continue
        result = labor_classifier.classify(
            title=item.title, summary=item.summary, section=item.section
        )
        if result.classification in {Classification.RELEVANT, Classification.REVIEW}:
            labor.append((item, result.topic_score))
    labor.sort(key=lambda pair: pair[1], reverse=True)
    broadcast = [
        item
        for item in articles
        if item.source in BROADCAST_SOURCES
        and item.classification in {Classification.RELEVANT, Classification.REVIEW}
    ]

    sections = [
        BriefingSection("I. 장애정책·장애인운동", cluster_issues(disability)),
        BriefingSection("II. 노동·돌봄·빈곤", cluster_issues([item for item, _score in labor])),
        BriefingSection("III. 방송 뉴스 중 장애 주제", cluster_issues(broadcast)),
    ]
    selected = [
        article for section in sections for issue in section.issues for article in issue.articles
    ]
    opinions = select_opinions(articles, selected)
    sections.append(BriefingSection("IV. 주요 칼럼", cluster_issues(opinions, max_issues=12)))
    failures = _source_failures(storage)
    counts = [len(section.issues) for section in sections]
    overview = (
        f"{kst_display(start)}부터 {kst_display(end)}까지 공개 발견 경로에 나타난 "
        f"{len(articles)}건의 기사 메타데이터를 비교하였다. 장애 의제 {counts[0]}개, "
        f"노동·돌봄·빈곤 의제 {counts[1]}개, 방송 장애 의제 {counts[2]}개와 주요 칼럼 "
        f"{counts[3]}개를 선별하였다. 자동 선별은 운동의 판단을 대체하지 않으며, "
        "수집 실패나 robots.txt 차단은 기사 부재가 아니라 확인 불능으로 해석해야 한다."
    )
    return BriefingDocument(
        report_date=report_date,
        start=start,
        end=end,
        overview=overview,
        sections=sections,
        source_failures=failures,
    )


def cluster_issues(articles: list[ArticleRecord], *, max_issues: int = 10) -> list[BriefingIssue]:
    clusters: list[list[ArticleRecord]] = []
    for article in articles:
        tokens = issue_tokens(article)
        target: list[ArticleRecord] | None = None
        for cluster in clusters:
            cluster_tokens = set().union(*(issue_tokens(item) for item in cluster))
            overlap = tokens & cluster_tokens
            if len(overlap) >= 2 or any(len(token) >= 5 for token in overlap):
                target = cluster
                break
        if target is None:
            if len(clusters) >= max_issues:
                continue
            clusters.append([article])
        elif len(target) < 5:
            target.append(article)
    issues: list[BriefingIssue] = []
    for cluster in clusters:
        lead = max(cluster, key=lambda item: item.topic_score)
        matches = _common_matches(cluster)
        assessment = _assessment(cluster, matches)
        refs = [
            f"{SOURCE_LABELS.get(item.source, item.source)} 공개 요약·메타데이터"
            for item in cluster
        ]
        issues.append(
            BriefingIssue(
                title=lead.title,
                articles=cluster,
                assessment=assessment,
                references=list(dict.fromkeys(refs)),
                crpd_articles=(
                    crpd_articles(" ".join(item.title for item in cluster))
                    if any(
                        item.classification in {Classification.RELEVANT, Classification.REVIEW}
                        for item in cluster
                    )
                    else []
                ),
            )
        )
    return issues


def is_opinion(article: ArticleRecord) -> bool:
    haystack = " ".join(value for value in (article.title, article.section or "") if value).lower()
    path = urlsplit(article.canonical_url).path.lower()
    return any(term in haystack for term in OPINION_TERMS) or any(
        marker in path for marker in OPINION_PATHS
    )


def select_opinions(
    articles: list[ArticleRecord], selected_issues: list[ArticleRecord]
) -> list[ArticleRecord]:
    selected_tokens = set().union(*(issue_tokens(item) for item in selected_issues))
    ranked: list[tuple[int, ArticleRecord]] = []
    for article in articles:
        if article.source not in PRINT_DIGITAL_SOURCES or not is_opinion(article):
            continue
        disability_opinion = (
            article.source in OPINION_FULL_SCAN_SOURCES | DISABILITY_PRESS_SOURCES
            and article.classification in {Classification.RELEVANT, Classification.REVIEW}
        )
        overlap = issue_tokens(article) & selected_tokens
        reverse_match = len(overlap) >= 2 or any(len(token) >= 5 for token in overlap)
        if disability_opinion or reverse_match:
            ranked.append((10 * int(disability_opinion) + len(overlap), article))
    ranked.sort(
        key=lambda pair: (pair[0], pair[1].published_at or pair[1].first_seen_at),
        reverse=True,
    )
    return [article for _score, article in ranked[:24]]


def issue_tokens(article: ArticleRecord) -> set[str]:
    tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", article.title.lower()))
    return {token for token in tokens if token not in STOPWORDS and not token.isdigit()}


def crpd_articles(text: str) -> list[str]:
    return [article for article, terms in CRPD_TERMS.items() if any(term in text for term in terms)]


def render_briefing_markdown(document: BriefingDocument, *, crpd_url: str | None) -> str:
    lines = [
        f"# 일간 장애정책·노동 뉴스 브리핑 ({document.report_date})",
        "",
        document.overview,
        "",
    ]
    for section in document.sections:
        lines.extend([f"# {section.title}", ""])
        if not section.issues:
            lines.extend(
                [
                    "공개 발견 경로와 현재 판별 규칙에서 선정된 기사가 없다. "
                    "수집 실패를 기사 부재로 해석해서는 안 된다.",
                    "",
                ]
            )
            continue
        for number, issue in enumerate(section.issues, start=1):
            lines.extend([f"## {number}. {issue.title}", "", "### 주요 언론 보도", ""])
            lines.extend(["| 언론사 | 기사 | 발행 |", "|---|---|---|"])
            for article in issue.articles:
                label = _markdown_table_text(article.title)
                lines.append(
                    f"| {SOURCE_LABELS.get(article.source, article.source)} | "
                    f"[{label}]({article.canonical_url}) | "
                    f"{kst_display(article.published_at)} |"
                )
            lines.extend(["", "### 오늘의 변화", "", issue.assessment, ""])
            lines.extend(["<details>", "<summary>추가 자료 · 더 찾아보기</summary>", ""])
            for reference in issue.references:
                lines.append(f"- {reference}")
            if issue.crpd_articles:
                label = ", ".join(issue.crpd_articles)
                if crpd_url:
                    lines.append(f"- [CRPD 조문별 통합참조표]({crpd_url}): {label}")
                else:
                    lines.append(f"- CRPD 연결: {label}")
            lines.extend(["", "</details>", ""])
    lines.extend(["# 점검", ""])
    if document.source_failures:
        lines.append("다음 출처는 실패·차단 상태이므로 해당 매체의 기사 부재를 뜻하지 않는다.")
        lines.extend(f"- {failure}" for failure in document.source_failures)
    else:
        lines.append("최근 건강상태에서 출처별 실패가 기록되지 않았다.")
    lines.extend(
        [
            "",
            "본문 원문은 저장하지 않았으며 제목·URL·공개 요약·판정 근거만 사용하였다.",
            "",
        ]
    )
    return "\n".join(lines)


def write_briefing(document: BriefingDocument, *, output_path: Path, crpd_url: str | None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_briefing_markdown(document, crpd_url=crpd_url), encoding="utf-8")
    return output_path


def _common_matches(articles: list[ArticleRecord]) -> list[str]:
    counts = Counter(term for article in articles for term in article.matched_terms)
    return [term for term, _count in counts.most_common(4)]


def _markdown_table_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
        .replace("\n", " ")
    )


def _assessment(articles: list[ArticleRecord], matches: list[str]) -> str:
    outlets = ", ".join(
        dict.fromkeys(SOURCE_LABELS.get(item.source, item.source) for item in articles)
    )
    issue = ", ".join(matches) if matches else "해당 의제"
    summaries = [item.summary for item in articles if item.summary]
    public_context = short_text(summaries[0], 180) if summaries else None
    base = (
        f"{outlets}의 보도를 함께 보면 {issue} 문제가 개별 사건이 아니라 권리와 "
        "공적 책임의 문제로 제기되고 있다. 자동 브리핑은 당사자 관점과 구조적 "
        "불평등을 중심에 두고 후속 정책·운동 대응을 확인할 필요가 있다고 판단한다."
    )
    return f'{base} 공개 요약상 핵심 맥락은 "{public_context}"이다.' if public_context else base


def _disability_priority(article: ArticleRecord) -> tuple[float, datetime]:
    movement_terms = (
        "투쟁",
        "농성",
        "자립",
        "탈시설",
        "인권",
        "차별",
        "교육권",
        "이동권",
        "노동권",
        "재수용",
    )
    source_bonus = (
        12.0
        if article.source == "beminor"
        else 5.0
        if article.source in DISABILITY_PRESS_SOURCES
        else 0.0
    )
    movement_bonus = 8.0 if any(term in article.title for term in movement_terms) else 0.0
    return (
        article.topic_score + source_bonus + movement_bonus,
        article.published_at or article.first_seen_at,
    )


def _source_failures(storage: JsonlStorage) -> list[str]:
    try:
        health = storage.health_path.read_text(encoding="utf-8")
    except OSError:
        return ["최근 건강상태 파일 없음"]
    import json

    try:
        payload = json.loads(health)
    except json.JSONDecodeError:
        return ["최근 건강상태 파일 손상"]
    state_sources = storage.load_state().get("sources", {})
    failures: list[str] = []
    sources = payload.get("sources", {}) if isinstance(payload, dict) else {}
    for source, label in SOURCE_LABELS.items():
        details = sources.get(source) if isinstance(sources, dict) else None
        if not isinstance(details, dict) and isinstance(state_sources, dict):
            details = state_sources.get(source)
        if not isinstance(details, dict):
            failures.append(f"{label}: 미실행")
        elif not details.get("success") or details.get("errors"):
            errors = details.get("errors") or ["상세 오류 없음"]
            failures.append(f"{label}: {str(errors[0])[:160]}")
    return failures
