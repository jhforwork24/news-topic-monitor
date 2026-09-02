from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .classifier import RuleClassifier
from .models import (
    ArticleRecord,
    Classification,
    EditorialPlan,
    EditorialSection,
    VerificationStatus,
)
from .sources import PRIMARY_COMPARISON_SOURCES, SOURCE_LABELS
from .storage import JsonlStorage
from .utils import KST, kst_display, short_text, stable_article_key

OPINION_TERMS = (
    "사설",
    "칼럼",
    "기고",
    "논설",
    "시론",
    "논단",
    "세계의 창",
    "묵묵",
    "세상읽기",
    "오피니언",
)
OPINION_PATHS = ("/opinion", "/column", "/editorial", "/contribution")
PHOTO_NEWS_TERMS = ("[포토뉴스]", "[사진]", "포토뉴스", "화보")
ENTERTAINMENT_SECTION_TERMS = (
    "연예",
    "방송연예",
    "연예뉴스",
    "음악",
    "스타",
    "스포츠",
)
ENTERTAINMENT_PATHS = (
    "/entertainments/",
    "/entertainment/",
    "/sports/",
    "/photo/",
    "/gallery/",
)
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
    "개최",
    "전원",
    "추진",
    "현장",
}
CONCEPT_TERMS = (
    "색동원",
    "거주인",
    "전원 자립",
    "서울시의회",
    "이동권",
    "권리중심공공일자리",
    "평택",
    "정신의료기관",
    "집단 전원",
    "섭지코지",
    "접근성",
    "부산",
    "장애아동",
    "발달장애",
    "지원센터",
    "최중증",
    "통합돌봄",
    "교육권",
    "CRPD",
    "국제장애인권컨퍼런스",
    "산업재해",
    "중대재해",
    "비정규직",
    "최저임금",
    "공공돌봄",
    "홈플러스",
    "현대차",
    "특별교통수단",
    "장애인콜택시",
)

# 편집단계에서 단순 홍보·의전성 보도는 제외한다. 다만 CRPD 채택 20주년처럼
# 그 해의 핵심 장애인권 의제와 결합한 행사는 낮은 순위로 남긴다.
PROMOTIONAL_TERMS = (
    "참가자 모집",
    "수강생 모집",
    "신청자 모집",
    "신청 접수",
    "선착순 모집",
)
ROUTINE_LOCAL_VISIT_TERMS = ("방문", "의견 청취", "현장 방문", "간담회")
LOCAL_LEGISLATURE_TERMS = ("도의회", "시의회", "군의회", "구의회")
MAJOR_EVENT_TERMS = (
    "학대",
    "성폭력",
    "폭행",
    "사망",
    "농성",
    "집단 전원",
    "전원 자립",
    "인권침해",
)

# 일간 브리핑의 상위 검토군을 먼저 고정한 뒤 홍보·의전성 보도를
# 제외한다. 제외된 자리를 차순위 홍보 보도로 다시 채우지 않는다.
DISABILITY_REVIEW_POOL_SIZE = 10

STRONG_PREVIOUS_CONCEPTS = frozenset(
    {
        "색동원",
        "전원 자립",
        "권리중심공공일자리",
        "정신의료기관",
        "집단 전원",
        "섭지코지",
        "최중증",
        "통합돌봄",
        "교육권",
        "국제장애인권컨퍼런스",
        "산업재해",
        "중대재해",
        "공공돌봄",
        "홈플러스",
        "현대차",
    }
)

KNOWN_PREVIOUS_COVERAGE = (
    (
        ("서울시의회", "권리중심"),
        "2026-08-12",
        "서울시의회, 오세훈 시장이 후퇴시킨 장애인 권리 되돌리나",
        "https://www.beminor.com/news/articleView.html?idxno=30268",
        "비마이너",
        "조례 발의의 배경과 임시회 표결 일정을 확인한 비마이너 선행 보도다.",
    ),
    (
        ("색동원", "자립"),
        "2026-06-08",
        "색동원 피해자 전원 탈시설 약속 이행 요구 천막농성 돌입",
        "https://www.imedialife.co.kr/news/articleView.html?idxno=66542",
        "imedialife.co.kr",
        "전원 자립계획·주거·활동지원 요구가 농성 시작 단계에서 제기되었다.",
    ),
)


@dataclass(frozen=True)
class PreviousCoverage:
    published: str
    label: str
    url: str | None
    outlet: str
    comparison: str


@dataclass
class BriefingIssue:
    title: str
    articles: list[ArticleRecord]
    summary: str
    tone_analysis: str
    previous_coverage: list[PreviousCoverage] = field(default_factory=list)
    keyword: str = ""


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
    telegram_summary: str
    sections: list[BriefingSection]
    source_failures: list[str]
    editorial_notes: list[str] = field(default_factory=list)
    editorially_selected_ids: list[str] = field(default_factory=list)


def build_briefing(
    storage: JsonlStorage,
    *,
    topics_path: Path,
    start: datetime,
    end: datetime,
    report_date: str,
) -> BriefingDocument:
    all_articles = list(storage.iter_articles())
    articles = [
        item for item in all_articles if start <= (item.published_at or item.first_seen_at) < end
    ]
    articles.sort(key=lambda item: item.published_at or item.first_seen_at, reverse=True)
    history = [item for item in all_articles if (item.published_at or item.first_seen_at) < start]
    editorial_notes: list[str] = []

    disability_candidates: list[ArticleRecord] = []
    for item in articles:
        if is_opinion(item):
            continue
        if item.classification == Classification.REVIEW:
            editorial_notes.append(f"I절 자동발행 제외 — {item.title}: 사람의 검토가 필요한 판정")
            continue
        if item.classification != Classification.RELEVANT:
            continue
        disability_candidates.append(item)

    anniversary_candidates = [
        item for item in disability_candidates if _is_crpd_anniversary_event([item])
    ]
    regular_candidates = [
        item for item in disability_candidates if item not in anniversary_candidates
    ]
    regular_candidates.sort(key=_disability_priority, reverse=True)
    # CRPD 20주년 같은 연간 핵심의제 행사는 일반 순위와 관계없이
    # 검토군 마지막에 두어 하나의 사안으로 묶는다.
    shortlist = regular_candidates[:DISABILITY_REVIEW_POOL_SIZE] + anniversary_candidates

    disability: list[ArticleRecord] = []
    for item in shortlist:
        reason = disability_editorial_exclusion(item)
        if reason:
            editorial_notes.append(f"I절 제외 — {item.title}: {reason}")
            continue
        disability.append(item)
    disability.sort(key=_disability_priority, reverse=True)
    disability_issues = cluster_issues(
        disability,
        history=history,
        max_issues=10,
    )
    disability_urls = {
        article.canonical_url for issue in disability_issues for article in issue.articles
    }

    labor_classifier = RuleClassifier(topics_path, topic="labor_care_poverty")
    labor: list[tuple[ArticleRecord, float]] = []
    for item in articles:
        if is_opinion(item):
            continue
        if item.canonical_url in disability_urls or item.classification in {
            Classification.RELEVANT,
            Classification.REVIEW,
        }:
            if "최중증 발달장애인 통합돌봄" in _article_text(item):
                editorial_notes.append(f"II절 제외·I절 배치 — {item.title}")
            continue
        reason = labor_editorial_exclusion(item)
        if reason:
            editorial_notes.append(f"II절 제외 — {item.title}: {reason}")
            continue
        result = labor_classifier.classify(
            title=item.title, summary=item.summary, section=item.section
        )
        if result.classification != Classification.RELEVANT:
            continue
        if "보양식 세트" in _article_text(item):
            editorial_notes.append(f"II절 제외 — {item.title}: 보양식 제공 여부 중심의 단발성 사안")
            continue
        labor.append((item, result.topic_score))
    labor.sort(key=lambda pair: pair[1], reverse=True)

    sections = [
        BriefingSection("I. 장애정책·장애인운동", disability_issues),
        BriefingSection(
            "II. 노동·돌봄·빈곤",
            cluster_issues(
                [item for item, _score in labor],
                history=history,
                max_issues=7,
            ),
        ),
    ]
    opinions = select_opinions(articles)
    opinion_issues = cluster_issues(
        opinions,
        history=history,
        max_issues=12,
    )
    if opinion_issues:
        sections.append(BriefingSection("III. 주요 칼럼", opinion_issues))
    else:
        editorial_notes.append("III절 생략 — 지정 칼럼과 지정 7개 매체 장애 관련 칼럼 없음")

    overview = build_overview(start, end, sections)
    telegram_summary = build_telegram_summary(sections)
    failures = _source_failures(storage)
    return BriefingDocument(
        report_date=report_date,
        start=start,
        end=end,
        overview=overview,
        telegram_summary=telegram_summary,
        sections=sections,
        source_failures=failures,
        editorial_notes=editorial_notes,
    )


def build_editorial_briefing(
    storage: JsonlStorage,
    *,
    plan: EditorialPlan,
    start: datetime,
    end: datetime,
    report_date: str,
) -> BriefingDocument:
    all_articles = list(storage.iter_articles())
    current = [
        article
        for article in all_articles
        if start <= (article.published_at or article.first_seen_at) < end
    ]
    history = [
        article
        for article in all_articles
        if (article.published_at or article.first_seen_at) < start
    ]
    by_id = {_editorial_candidate_id(article): article for article in current}
    issues_by_section: dict[EditorialSection, list[BriefingIssue]] = {
        section: [] for section in EditorialSection
    }
    selected_ids: list[str] = []

    for decision in plan.issues:
        missing = [
            candidate_id for candidate_id in decision.candidate_ids if candidate_id not in by_id
        ]
        if missing:
            raise ValueError(f"editorial plan contains {len(missing)} unavailable candidate IDs")
        articles = _sort_cluster_articles(
            [by_id[candidate_id] for candidate_id in decision.candidate_ids]
        )
        selected_ids.extend(decision.candidate_ids)
        issues_by_section[decision.section].append(
            BriefingIssue(
                title=decision.title,
                articles=articles,
                summary=decision.summary,
                tone_analysis=decision.tone_analysis,
                previous_coverage=previous_coverage_for(articles, history),
                keyword=decision.keyword,
            )
        )

    sections = [
        BriefingSection("I. 장애정책·장애인운동", issues_by_section[EditorialSection.DISABILITY]),
        BriefingSection("II. 노동·돌봄·빈곤", issues_by_section[EditorialSection.LABOR]),
    ]
    opinion_issues = issues_by_section[EditorialSection.OPINION]
    if opinion_issues:
        sections.append(BriefingSection("III. 주요 칼럼", opinion_issues))

    editorial_notes: list[str] = []
    for exclusion in plan.exclusions[:20]:
        article = by_id.get(exclusion.candidate_id)
        if article is not None:
            editorial_notes.append(f"GPT 편집 제외 — {article.title}: {exclusion.reason}")
    if not opinion_issues:
        editorial_notes.append("III절 생략 — GPT 편집에서 최종 선정된 칼럼 없음")

    return BriefingDocument(
        report_date=report_date,
        start=start,
        end=end,
        overview=build_overview(start, end, sections),
        telegram_summary=build_telegram_summary(sections),
        sections=sections,
        source_failures=_source_failures(storage),
        editorial_notes=editorial_notes,
        editorially_selected_ids=selected_ids,
    )


def disability_editorial_exclusion(article: ArticleRecord) -> str | None:
    text = _article_text(article)
    title = article.title
    recruitment = "모집" in title and any(
        term in title for term in ("장학생", "참가자", "수강생", "신청자")
    )
    if recruitment or any(term in title for term in PROMOTIONAL_TERMS):
        return "모집·접수 중심의 단순 홍보성 보도"
    if any(term in title for term in ("연애 고민", "서장훈 분노", "채용 박람회")):
        return "정책·권리 변화보다 예능·행사 홍보에 중심을 둔 보도"
    routine_notice = any(
        (
            "재난안전가이드" in title and "배포" in title,
            "이용 안내" in title,
            "간담회" in title and "개최" in title,
            "귀성길" in title and "지원" in title,
            "장애인복지·공익채널" in title and "선정 접수" in title,
        )
    )
    if "부모대회" in title or routine_notice:
        return "정책 변화가 확인되지 않은 지역·단체 행사성 보도"
    local_visit = any(term in text for term in LOCAL_LEGISLATURE_TERMS) and any(
        term in text for term in ROUTINE_LOCAL_VISIT_TERMS
    )
    if local_visit and not any(term in text for term in MAJOR_EVENT_TERMS):
        return "특수한 권리침해·투쟁 맥락이 없는 지역의회 인사의 일상적 방문"
    return None


def labor_editorial_exclusion(article: ArticleRecord) -> str | None:
    title = article.title
    section = (article.section or "").lower()
    path = urlsplit(article.canonical_url).path.lower()
    if any(term.lower() in title.lower() for term in PHOTO_NEWS_TERMS):
        return "사진·화보 중심 보도"
    if any(term.lower() in section for term in ENTERTAINMENT_SECTION_TERMS):
        return "연예·스포츠 섹션 보도"
    if any(marker in path for marker in ENTERTAINMENT_PATHS):
        return "연예·스포츠 경로의 보도"
    return None


def cluster_issues(
    articles: list[ArticleRecord],
    *,
    history: list[ArticleRecord] | None = None,
    max_issues: int = 10,
) -> list[BriefingIssue]:
    anniversary_articles = [
        article for article in articles if _is_crpd_anniversary_event([article])
    ]
    regular_articles = [article for article in articles if article not in anniversary_articles]
    reserved = 1 if anniversary_articles else 0
    clusters = _cluster_article_groups(regular_articles, max_issues=max_issues - reserved)
    if anniversary_articles:
        clusters.extend(_cluster_article_groups(anniversary_articles, max_issues=1))
    return [_briefing_issue(cluster, history or []) for cluster in clusters]


def _cluster_article_groups(
    articles: list[ArticleRecord], *, max_issues: int
) -> list[list[ArticleRecord]]:
    clusters: list[list[ArticleRecord]] = []
    for article in articles:
        tokens = issue_tokens(article)
        target: list[ArticleRecord] | None = None
        for cluster in clusters:
            if not _can_cluster(article, cluster):
                continue
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
    return clusters


def _briefing_issue(
    cluster: list[ArticleRecord],
    history: list[ArticleRecord],
) -> BriefingIssue:
    lead = max(cluster, key=lambda item: (_disability_priority(item), item.topic_score))
    articles = _sort_cluster_articles(cluster)
    previous = previous_coverage_for(articles, history)
    return BriefingIssue(
        title=lead.title,
        articles=articles,
        summary=summarize_issue(articles),
        tone_analysis=analyze_tone(articles),
        previous_coverage=previous,
    )


def is_opinion(article: ArticleRecord) -> bool:
    # 본문·요약의 '사설구급차' 같은 단어를 '사설' 표지로 오인하지
    # 않도록 편집 유형을 드러내는 제목·섹션·URL만 판정한다.
    haystack = " ".join(value for value in (article.title, article.section) if value).lower()
    path = urlsplit(article.canonical_url).path.lower()
    return any(term.lower() in haystack for term in OPINION_TERMS) or any(
        marker in path for marker in OPINION_PATHS
    )


def editorial_opinion_allowed(article: ArticleRecord) -> bool:
    text = " ".join(
        value
        for value in (article.title, article.byline, article.section, article.summary)
        if value
    )
    mandatory = (
        (article.source == "hani" and "세계의 창" in text and "지제크" in text)
        or (article.source == "mediaus" and "김민하" in text)
        or (article.source == "khan" and "고병권" in text and "묵묵" in text)
    )
    return mandatory or (article.source in PRIMARY_COMPARISON_SOURCES and is_opinion(article))


def select_opinions(articles: list[ArticleRecord]) -> list[ArticleRecord]:
    selected: list[ArticleRecord] = []
    for article in articles:
        text = _article_text(article)
        mandatory = (
            (article.source == "hani" and "세계의 창" in text and "지제크" in text)
            or (article.source == "mediaus" and "김민하" in text)
            or (article.source == "khan" and "고병권" in text and "묵묵" in text)
        )
        if not mandatory and not is_opinion(article):
            continue
        disability_column = (
            article.source in PRIMARY_COMPARISON_SOURCES
            and article.classification == Classification.RELEVANT
        )
        if mandatory or disability_column:
            selected.append(article)
    selected.sort(key=lambda item: item.published_at or item.first_seen_at, reverse=True)
    return selected


def summarize_issue(articles: list[ArticleRecord]) -> str:
    facts: list[str] = []
    for article in articles[:5]:
        for sentence in _clean_summary_sentences(article.summary):
            if any(_similar_text(sentence, existing) for existing in facts):
                continue
            facts.append(sentence)
            if len(facts) >= 3:
                break
        if len(facts) >= 3:
            break
    if facts:
        while len(" ".join(facts)) > 560 and len(facts) > 1:
            facts.pop()
        return " ".join(facts)
    title = _neutral_text(articles[0].title).rstrip(". ")
    particle = _korean_particle(title, "과", "와")
    return f"당일 보도에서는 {title}{particle} 관련한 사실관계와 공적 책임의 쟁점이 다뤄졌다."


def analyze_tone(articles: list[ArticleRecord]) -> str:
    if len(articles) == 1:
        article = articles[0]
        focus = _tone_focus(article)
        if focus == "사건의 사실관계와 공적 책임을 중심으로 전달하는 논조다.":
            return ""
        label = SOURCE_LABELS.get(article.source, article.source)
        return f"{label}{_korean_particle(label, '은', '는')} {focus}"

    grouped: dict[str, list[str]] = {}
    for article in articles:
        focus = _tone_focus(article)
        label = SOURCE_LABELS.get(article.source, article.source)
        labels = grouped.setdefault(focus, [])
        if label not in labels:
            labels.append(label)
    descriptions: list[str] = []
    for focus, labels in list(grouped.items())[:4]:
        subject = "·".join(labels)
        descriptions.append(f"{subject} 보도는 {focus.removeprefix('사건의 ').rstrip('.')}" + ".")
    return " ".join(descriptions)


def previous_coverage_for(
    articles: list[ArticleRecord], history: list[ArticleRecord]
) -> list[PreviousCoverage]:
    """Pick 1-3 prior articles on the same topic, ranked by relevance then detail.

    Relevance is the shared-token overlap with the current issue. Detail is a proxy
    for how substantive the earlier article was — body text is never retained past
    the collection run, so verification status, designated-source membership, and
    summary length stand in for "was this a thorough report or a short notice".
    """

    text = " ".join(_article_text(article) for article in articles)
    result: list[PreviousCoverage] = []
    for triggers, published, label, url, outlet, comparison in KNOWN_PREVIOUS_COVERAGE:
        if all(trigger in text for trigger in triggers):
            result.append(PreviousCoverage(published, label, url, outlet, comparison))

    tokens = set().union(*(issue_tokens(article) for article in articles))
    strong_tokens = {term.lower() for term in STRONG_PREVIOUS_CONCEPTS} & tokens
    current_urls = {article.canonical_url for article in articles}
    candidates: list[tuple[int, float, datetime, ArticleRecord]] = []
    for article in history:
        if article.canonical_url in current_urls or is_opinion(article):
            continue
        overlap = tokens & issue_tokens(article)
        if not overlap & strong_tokens and len(overlap) < 4:
            continue
        candidates.append(
            (
                len(overlap),
                _detail_score(article),
                article.published_at or article.first_seen_at,
                article,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    known_urls = {item.url for item in result}
    for _relevance, _detail, published, article in candidates:
        if article.canonical_url in known_urls:
            continue
        result.append(
            PreviousCoverage(
                published.astimezone(KST).date().isoformat(),
                article.title,
                article.canonical_url,
                SOURCE_LABELS.get(article.source, article.source),
                _previous_coverage_summary(article),
            )
        )
        known_urls.add(article.canonical_url)
        if len(result) >= 3:
            break
    return result[:3]


def _previous_coverage_summary(article: ArticleRecord) -> str:
    """One sentence on what the prior article actually covered, not a generic prompt."""

    sentences = _clean_summary_sentences(article.summary)
    if sentences:
        return sentences[0]
    title = _neutral_text(article.title).rstrip(". ")
    particle = _korean_particle(title, "을", "를")
    return f"{title}{particle} 보도했다."


def _detail_score(article: ArticleRecord) -> float:
    score = 0.0
    if article.verification_status == VerificationStatus.BODY_VERIFIED:
        score += 3.0
    if article.source in PRIMARY_COMPARISON_SOURCES:
        score += 1.0
    score += min(len(article.summary or ""), 400) / 400.0
    return score


def build_overview(start: datetime, end: datetime, sections: list[BriefingSection]) -> str:
    by_title = {section.title: section for section in sections}
    section_sentences = [
        f"이번 브리핑은 {kst_display(start)}부터 {kst_display(end)}까지 보도된 사안 가운데 "
        "장애인의 시민권과 노동자·빈곤 당사자의 생명·생존권에 영향을 미치는 내용을 "
        "정리하였다."
    ]
    disability = by_title.get("I. 장애정책·장애인운동")
    if disability and disability.issues:
        section_sentences.append(
            "장애정책·장애인운동에서는 "
            + _issue_keywords(disability.issues, 4)
            + " 등을 중심으로 지역사회에서 살아갈 권리와 공적 책임의 이행 여부를 살폈다."
        )
    labor = by_title.get("II. 노동·돌봄·빈곤")
    if labor and labor.issues:
        section_sentences.append(
            "노동·돌봄·빈곤 부문에서는 "
            + _issue_keywords(labor.issues, 4)
            + " 등을 통해 원청·사용자와 국가의 책임을 점검하였다."
        )
    columns = by_title.get("III. 주요 칼럼")
    if columns and columns.issues:
        section_sentences.append(
            "주요 칼럼으로는 " + _issue_keywords(columns.issues, 3) + "을 함께 소개한다."
        )
    return " ".join(section_sentences)


def build_telegram_summary(sections: list[BriefingSection]) -> str:
    by_title = {section.title: section for section in sections}
    sentences: list[str] = []
    disability = by_title.get("I. 장애정책·장애인운동")
    if disability and disability.issues:
        sentences.append(
            "장애 의제에서는 "
            + _issue_keywords(disability.issues, 4)
            + "을 주요 후속 감시 대상으로 정리하였다."
        )
    labor = by_title.get("II. 노동·돌봄·빈곤")
    if labor and labor.issues:
        labor_titles = _issue_keywords(labor.issues, 3)
        sentences.append(
            "노동·돌봄·빈곤 의제에서는 "
            + labor_titles
            + _korean_particle(labor_titles, "을", "를")
            + " 중심으로 생명·고용·생존권을 살폈다."
        )
    if not sentences:
        sentences.append(
            "당일 보도에서 장애인의 시민권과 노동자의 생존권 관련 핵심 의제를 정리하였다."
        )
    return " ".join(sentences[:6])


def render_briefing_markdown(document: BriefingDocument, *, crpd_url: str | None) -> str:
    del crpd_url
    lines = [
        f"# 일간 장애정책·노동 뉴스 브리핑 ({document.report_date})",
        "",
        document.overview,
        "",
    ]
    for section in document.sections:
        if section.title == "III. 주요 칼럼" and not section.issues:
            continue
        lines.extend([f"# {section.title}", ""])
        for number, issue in enumerate(section.issues, start=1):
            lines.extend([f"## {number}. {issue.title}", "", "### 주요 언론 보도", ""])
            for article in issue.articles:
                label = _markdown_table_text(article.title)
                lines.append(
                    f"- {article_listing_prefix(article)}[{label}]({article.canonical_url})"
                )
            lines.extend(
                [
                    "",
                    "### 이슈 요약·보도 논조",
                    "",
                    issue_analysis_text(issue),
                    "",
                ]
            )
            if issue.previous_coverage:
                lines.extend(["<details>", "<summary>동일 주제 이전 보도</summary>", ""])
                lines.extend(
                    [
                        "| 자료 | 날짜·매체 | 이전 보도 요약 |",
                        "|---|---|---|",
                    ]
                )
                for item in issue.previous_coverage[:3]:
                    label = _markdown_table_text(item.label)
                    material = f"[{label}]({item.url})" if item.url else label
                    meta = _markdown_table_text(f"{item.published} · {item.outlet}")
                    summary = _markdown_table_text(item.comparison)
                    lines.append(f"| {material} | {meta} | {summary} |")
                lines.extend(["", "</details>", ""])
    return "\n".join(lines)


def issue_analysis_text(issue: BriefingIssue) -> str:
    return " ".join(part for part in (issue.summary, issue.tone_analysis) if part)


def article_listing_prefix(article: ArticleRecord) -> str:
    parts = [SOURCE_LABELS.get(article.source, article.source)]
    if article.byline:
        parts.append(article.byline)
    parts.append(kst_display(article.published_at))
    return " · ".join(parts) + " — "


def render_editorial_report(document: BriefingDocument, *, page_url: str | None = None) -> str:
    lines = [
        f"# GitHub 브리핑 보고사항 ({document.report_date})",
        "",
        f"조사기간: {kst_display(document.start)} ~ {kst_display(document.end)}",
        "",
    ]
    if page_url:
        lines.extend([f"발행 문건: {page_url}", ""])
    lines.extend(["## 편집·분류 기록", ""])
    lines.extend(f"- {note}" for note in document.editorial_notes)
    if not document.editorial_notes:
        lines.append("- 별도 편집 제외·이동 기록 없음")
    lines.extend(["", "## 출처 점검", ""])
    if document.source_failures:
        lines.extend(f"- {failure}" for failure in document.source_failures)
    else:
        lines.append("- 최근 수집 health에 실패 출처 없음")
    return "\n".join(lines) + "\n"


def write_briefing(document: BriefingDocument, *, output_path: Path, crpd_url: str | None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_briefing_markdown(document, crpd_url=crpd_url), encoding="utf-8")
    return output_path


def _article_text(article: ArticleRecord) -> str:
    return " ".join(value for value in (article.title, article.section, article.summary) if value)


def issue_tokens(article: ArticleRecord) -> set[str]:
    text = _article_text(article)
    tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", article.title.lower()))
    tokens = {token for token in tokens if token not in STOPWORDS and not token.isdigit()}
    tokens.update(term.lower() for term in CONCEPT_TERMS if term.lower() in text.lower())
    return tokens


def _clean_summary_sentences(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = re.sub(r"^【[^】]+】\s*", "", value).strip()
    cleaned = re.sub(r"^\[[^\]]{1,40}\]\s*", "", cleaned).strip()
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = _remove_quoted_spans(cleaned)
    cleaned = re.sub(r"""(?<=[.!?])(?=[가-힣A-Z0-9"'])""", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    unique: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or "..." in sentence or "…" in sentence or sentence.endswith("?"):
            continue
        if sentence.endswith(("인데요.", "는데요.")):
            continue
        if any(marker in sentence for marker in ('"', "'", "“", "”", "\u2018", "\u2019")):
            continue
        sentence = _neutral_text(sentence)
        sentence = _conversational_to_plain(sentence)
        if sentence.startswith("(") and sentence.rstrip(".").endswith(")"):
            continue
        if not sentence.endswith("."):
            if re.search(r"(?:은|는|이|가|을|를|와|과|의|며|고|로|에)$", sentence):
                continue
            if len(sentence) > 120 or sentence.count("(") != sentence.count(")"):
                continue
            sentence = sentence.rstrip("!? ") + "."
        if len(sentence) > 260 or any(_similar_text(sentence, item) for item in unique):
            continue
        unique.append(sentence)
        if len(unique) >= 2:
            break
    return unique


def _neutral_text(value: str) -> str:
    cleaned = re.sub(r"[\"'“”\u2018\u2019]", "", value)
    cleaned = re.sub(r"^\[[^\]]{1,40}\]\s*", "", cleaned)
    cleaned = re.sub(r"\.{3}|…", " ", cleaned)
    cleaned = cleaned.replace("?", " ").replace("!", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _remove_quoted_spans(value: str) -> str:
    cleaned = value
    for opening, closing in (("“", "”"),):
        while opening in cleaned:
            start = cleaned.find(opening)
            end = cleaned.find(closing, start + 1)
            if end < 0:
                cleaned = cleaned[:start]
                break
            cleaned = cleaned[:start] + " " + cleaned[end + 1 :]
    while '"' in cleaned:
        start = cleaned.find('"')
        end = cleaned.find('"', start + 1)
        if end < 0:
            cleaned = cleaned[:start]
            break
        cleaned = cleaned[:start] + " " + cleaned[end + 1 :]
    return cleaned


def _conversational_to_plain(value: str) -> str:
    value = re.sub(r"^(?:네|예),?\s*", "", value)
    replacements = (
        ("했습니다.", "했다."),
        ("됐습니다.", "됐다."),
        ("됩니다.", "된다."),
        ("있습니다.", "있다."),
        ("없습니다.", "없다."),
        ("입니다.", "이다."),
        ("합니다.", "한다."),
        ("집니다.", "진다."),
        ("했습니다죠.", "했다."),
        ("했죠.", "했다."),
        ("됐죠.", "됐다."),
        ("있죠.", "있다."),
        ("없죠.", "없다."),
    )
    for old, new in replacements:
        if value.endswith(old):
            return value[: -len(old)] + new
    if value.endswith("다고요."):
        return value[: -len("다고요.")] + "다는 지적이 제기됐다."
    if value.endswith("습니다."):
        return value[: -len("습니다.")] + "다."
    return value


def _similar_text(left: str, right: str) -> bool:
    left_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", left.lower()))
    right_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", right.lower()))
    if not left_tokens or not right_tokens:
        return left == right
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) >= 0.5


def _tone_focus(article: ArticleRecord) -> str:
    text = _article_text(article)
    if article.source == "beminor":
        return (
            "당사자와 장애인운동의 요구를 중심에 두고 제도·시설의 구조적 책임을 "
            "드러내는 권리 중심 논조다."
        )
    if article.source in {"ablenews", "theindigo"}:
        return "장애계·공공기관의 발표와 요구를 충실히 전달하는 장애 전문매체의 정보 전달형 논조다."
    if any(term in text for term in ("농성", "파업", "투쟁", "성명", "촉구")):
        return "당사자·노동조합·시민사회의 요구와 집단행동의 배경을 강조하는 논조다."
    if any(term in text for term in ("발표", "수용", "개소", "지원", "계획", "권고")):
        return "정부·지자체·공공기관의 발표와 행정조치를 중심으로 전하는 절차·정책 중심 논조다."
    if article.source in {"chosun", "joongang", "donga"}:
        return "정책의 비용·갈등·행정 영향을 상대적으로 강조하는 보수 종합지의 논조다."
    return "사건의 사실관계와 공적 책임을 중심으로 전달하는 논조다."


def _sort_cluster_articles(cluster: list[ArticleRecord]) -> list[ArticleRecord]:
    return sorted(
        cluster,
        key=lambda item: (item.published_at or item.first_seen_at, item.topic_score),
    )


def _can_cluster(article: ArticleRecord, cluster: list[ArticleRecord]) -> bool:
    article_text = _article_text(article)
    cluster_text = " ".join(_article_text(item) for item in cluster)
    article_integrated_care = "최중증" in article_text and "통합돌봄" in article_text
    cluster_integrated_care = "최중증" in cluster_text and "통합돌봄" in cluster_text
    if article_integrated_care != cluster_integrated_care:
        return False
    if "지원센터" in article_text and "지원센터" in cluster_text:
        article_regions = {region for region in ("부산", "대전") if region in article_text}
        cluster_regions = {region for region in ("부산", "대전") if region in cluster_text}
        if article_regions and cluster_regions and article_regions.isdisjoint(cluster_regions):
            return False
    return True


def _issue_keywords(issues: list[BriefingIssue], limit: int) -> str:
    labels = []
    for issue in issues[:limit]:
        label = issue.keyword.strip() if issue.keyword else ""
        if not label:
            title = re.sub(r"\s*\(\d{4}\.\d{2}\.\d{2}/[^)]*\)$", "", issue.title)
            label = short_text(title, 65) or title
        labels.append(label)
    return "·".join(labels)


def _korean_particle(value: str, consonant: str, vowel: str) -> str:
    hangul = next((char for char in reversed(value) if "가" <= char <= "힣"), None)
    if hangul is None:
        return vowel
    return consonant if (ord(hangul) - ord("가")) % 28 else vowel


def _is_crpd_anniversary_event(articles: list[ArticleRecord]) -> bool:
    text = " ".join(_article_text(article) for article in articles)
    return "CRPD" in text and "20주년" in text and any(term in text for term in ("개최", "행사"))


def _markdown_table_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
        .replace("\n", " ")
    )


def _disability_priority(article: ArticleRecord) -> tuple[float, datetime]:
    text = _article_text(article)
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
        "집단 전원",
    )
    score = article.topic_score + 10 * sum(term in text for term in movement_terms)
    priority_terms = (
        ("색동원", 1000),
        ("권리중심공공일자리", 900),
        ("집단 전원", 800),
        ("섭지코지", 700),
        ("부산광역시장애아동", 600),
        ("교육권", 500),
        ("최중증 발달장애인 통합돌봄", 400),
    )
    score += sum(bonus for term, bonus in priority_terms if term in text)
    if _is_crpd_anniversary_event([article]):
        score = -1.0
    return score, article.published_at or article.first_seen_at


def _source_failures(storage: JsonlStorage) -> list[str]:
    if not storage.health_path.exists():
        return []
    try:
        health = storage.load_state() if storage.health_path == storage.state_path else None
        if health is None:
            import json

            health = json.loads(storage.health_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ["출처 health 파일을 읽지 못함"]
    failures: list[str] = []
    for source, detail in health.get("sources", {}).items():
        if detail.get("success"):
            continue
        errors = detail.get("errors") or []
        label = SOURCE_LABELS.get(source, source)
        failures.append(f"{label}: {errors[0] if errors else '수집 실패'}")
    return failures


def _editorial_candidate_id(article: ArticleRecord) -> str:
    return stable_article_key(
        article.source,
        article.canonical_url,
        article.article_id,
        article.title,
        article.published_at,
    )
