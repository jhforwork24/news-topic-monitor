from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .classifier import RuleClassifier
from .models import ArticleRecord, Classification
from .sources import BROADCAST_SOURCES, PRIMARY_COMPARISON_SOURCES, SOURCE_LABELS
from .storage import JsonlStorage
from .utils import KST, kst_display, short_text

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

CRPD_TERMS = {
    "제4조 일반의무": ("당사자 참여", "장애인단체", "정책 결정", "협의"),
    "제5조 평등과 비차별": ("차별", "평등", "혐오"),
    "제6조 장애여성": ("장애여성", "여성장애"),
    "제7조 장애아동": ("장애아동", "발달장애 아동"),
    "제8조 인식제고": ("인식개선", "권리중심공공일자리"),
    "제9조 접근성": ("접근", "편의시설", "정보접근", "섭지코지"),
    "제14조 개인의 자유와 안전": ("강제입원", "비자의 입원", "집단 전원"),
    "제16조 착취·폭력·학대로부터의 자유": ("학대", "폭력", "인권침해", "성폭력"),
    "제19조 자립생활 및 지역사회 통합": (
        "탈시설",
        "자립생활",
        "지역사회",
        "전원 자립",
        "집단 전원",
    ),
    "제20조 개인의 이동": ("이동권", "교통약자", "휠체어"),
    "제24조 교육": ("교육권", "특수교육", "통합교육"),
    "제25조 건강": ("건강권", "의료접근", "병원", "정신의료기관"),
    "제27조 근로 및 고용": ("노동", "고용", "일자리", "해고"),
    "제28조 적절한 생활수준과 사회적 보호": ("연금", "소득", "빈곤", "생계"),
    "제29조 정치 및 공적 생활 참여": ("참정권", "선거", "정치참여"),
    "제30조 문화·여가·스포츠 참여": (
        "장애인 관광",
        "문화 향유",
        "장애인 스포츠",
        "장애인체육",
    ),
    "제32조 국제협력": ("ODA", "국제개발협력", "국제장애인권컨퍼런스"),
    "제33조 국내 이행과 모니터링": ("국가인권위원회", "인권위 권고", "모니터링"),
}

CRPD_FULL_TEXT = (
    "장애인권리협약 및 선택의정서 전문(KDF)",
    "https://thekdf.org/un/?q=YToyOntzOjEyOiJrZXl3b3JkX3R5cGUiO3M6MzoiYWxsIjt"
    "zOjQ6InBhZ2UiO2k6MTt9&bmode=view&idx=171135234&t=board",
)
GENERAL_COMMENTS = {
    "제4조": (
        "일반논평 제7호 — 장애인 참여·포용",
        "https://uhr.humanrights.go.kr/pub/uhrstd/380",
    ),
    "제5조": (
        "일반논평 제6호 — 평등과 비차별",
        "https://uhr.humanrights.go.kr/pub/uhrstd/375",
    ),
    "제6조": (
        "일반논평 제3호 — 장애 여성·여아",
        "https://uhr.humanrights.go.kr/pub/uhrstd/368",
    ),
    "제9조": (
        "일반논평 제2호 — 접근성",
        "https://uhr.humanrights.go.kr/pub/uhrstd/366",
    ),
    "제12조": (
        "일반논평 제1호 — 법 앞의 평등",
        "https://uhr.humanrights.go.kr/pub/uhrstd/363",
    ),
    "제19조": (
        "일반논평 제5호 — 자립생활·지역사회 포용",
        "https://uhr.humanrights.go.kr/pub/uhrstd/373",
    ),
    "제24조": (
        "일반논평 제4호 — 포용적 교육",
        "https://uhr.humanrights.go.kr/pub/uhrstd/371",
    ),
    "제27조": (
        "일반논평 제8호 — 노동과 고용",
        "https://uhr.humanrights.go.kr/pub/uhrstd/834",
    ),
    "제33조": (
        "일반논평 제7호 — 장애인 참여·포용",
        "https://uhr.humanrights.go.kr/pub/uhrstd/380",
    ),
}
DEINSTITUTIONALIZATION_GUIDELINES = (
    "유엔장애인권리위원회 탈시설 가이드라인(KDF)",
    "https://thekdf.org/un/?q=YToyOntzOjEyOiJrZXl3b3JkX3R5cGUiO3M6MzoiYWxsIjt"
    "zOjQ6InBhZ2UiO2k6MTt9&bmode=view&idx=171135757&t=board",
)

# 일간 브리핑의 상위 검토군을 먼저 고정한 뒤 홍보·의전성 보도를
# 제외한다. 제외된 자리를 차순위 홍보 보도로 다시 채우지 않는다.
DISABILITY_REVIEW_POOL_SIZE = 10

LAW_REFERENCES = {
    "이동권": ("교통약자의 이동편의 증진법", "https://www.law.go.kr/법령/교통약자의이동편의증진법"),
    "접근성": (
        "장애인차별금지 및 권리구제 등에 관한 법률",
        "https://www.law.go.kr/법령/장애인차별금지및권리구제등에관한법률",
    ),
    "활동지원": (
        "장애인활동 지원에 관한 법률",
        "https://www.law.go.kr/법령/장애인활동지원에관한법률",
    ),
    "거주시설": ("장애인복지법", "https://www.law.go.kr/법령/장애인복지법"),
    "정신의료기관": (
        "정신건강증진 및 정신질환자 복지서비스 지원에 관한 법률",
        "https://www.law.go.kr/법령/정신건강증진및정신질환자복지서비스지원에관한법률",
    ),
    "교육권": (
        "장애인 등에 대한 특수교육법",
        "https://www.law.go.kr/법령/장애인등에대한특수교육법",
    ),
    "권리중심": (
        "장애인고용촉진 및 직업재활법",
        "https://www.law.go.kr/법령/장애인고용촉진및직업재활법",
    ),
    "장애아동": (
        "장애아동 복지지원법",
        "https://www.law.go.kr/법령/장애아동복지지원법",
    ),
    "발달장애": (
        "발달장애인 권리보장 및 지원에 관한 법률",
        "https://www.law.go.kr/법령/발달장애인권리보장및지원에관한법률",
    ),
    "최저임금": ("최저임금법", "https://www.law.go.kr/법령/최저임금법"),
    "산업재해": ("산업안전보건법", "https://www.law.go.kr/법령/산업안전보건법"),
    "중대재해": (
        "중대재해 처벌 등에 관한 법률",
        "https://www.law.go.kr/법령/중대재해처벌등에관한법률",
    ),
    "노조": ("노동조합 및 노동관계조정법", "https://www.law.go.kr/법령/노동조합및노동관계조정법"),
    "돌봄": ("근로기준법", "https://www.law.go.kr/법령/근로기준법"),
    "홈플러스": ("고용보험법", "https://www.law.go.kr/법령/고용보험법"),
}

LABOR_ONLY_REFERENCE_KEYS = frozenset(
    {"최저임금", "산업재해", "중대재해", "노조", "돌봄", "홈플러스"}
)

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

RESEARCH_REFERENCES = {
    "탈시설": (
        "한국보건사회연구원, 탈시설 장애인의 지역사회 정착 경로에 관한 연구",
        "https://www.kihasa.re.kr/publish/report/view?page=19&seq=27968&type=research",
    ),
    "이동권": (
        "국회입법조사처, 장애인의 지역 간 이동 편의 증진을 위한 교통 서비스 실태 및 개선방안",
        "https://www.nars.go.kr/report/view.do?brdSeq=26775&cmsCode=CM0156",
    ),
    "권리중심": (
        "한국장애인개발원, 중증장애인 자립지원과 장애인일자리사업 연계 연구",
        "https://www.koddi.or.kr/data/news_view.jsp?brdNum=7429361",
    ),
    "정신의료기관": (
        "한국장애인개발원, 정신장애인 자립생활 지원 방안 연구",
        "https://www.koddi.or.kr/system/download.jsp?filePath=%2Fhp_board%2FATT1%2F20230106161839001.pdf",
    ),
    "발달장애": (
        "한국장애인개발원 발달장애인 정책 연구자료",
        "https://www.koddi.or.kr/data/research.jsp",
    ),
    "산업재해": (
        "산업안전보건공단 산업재해 통계·사례",
        "https://www.kosha.or.kr/kosha/data/industrialAccidentStatus.do",
    ),
    "최저임금": (
        "최저임금위원회 심의·영향률 원자료",
        "https://www.minimumwage.go.kr/minWage/policy/influenceMain.do?division=E",
    ),
    "돌봄": (
        "육아정책연구소·여성가족부 아이돌봄서비스 실태조사",
        "https://repo.kicce.re.kr/handle/2019.oak/5639",
    ),
}

KNOWN_PREVIOUS_COVERAGE = (
    (
        ("서울시의회", "권리중심"),
        "2026-08-12",
        "서울시의회, 오세훈 시장이 후퇴시킨 장애인 권리 되돌리나",
        "https://www.beminor.com/news/articleView.html?idxno=30268",
        "조례 발의의 배경과 임시회 표결 일정을 확인한 비마이너 선행 보도다.",
    ),
    (
        ("색동원", "자립"),
        "2026-06-08",
        "색동원 피해자 전원 탈시설 약속 이행 요구 천막농성 돌입",
        "https://www.imedialife.co.kr/news/articleView.html?idxno=66542",
        "전원 자립계획·주거·활동지원 요구가 농성 시작 단계에서 제기되었다.",
    ),
)


@dataclass(frozen=True)
class PreviousCoverage:
    published: str
    label: str
    url: str | None
    comparison: str


@dataclass(frozen=True)
class BriefingReference:
    category: str
    label: str
    url: str | None
    note: str


@dataclass
class BriefingIssue:
    title: str
    articles: list[ArticleRecord]
    summary: str
    tone_analysis: str
    previous_coverage: list[PreviousCoverage] = field(default_factory=list)
    references: list[BriefingReference] = field(default_factory=list)
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
    telegram_summary: str
    sections: list[BriefingSection]
    source_failures: list[str]
    editorial_notes: list[str] = field(default_factory=list)


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
        if item.source in BROADCAST_SOURCES or is_opinion(item):
            continue
        if item.classification not in {Classification.RELEVANT, Classification.REVIEW}:
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
    disability_issues = cluster_issues(disability, history=history, max_issues=10)
    disability_urls = {
        article.canonical_url for issue in disability_issues for article in issue.articles
    }

    labor_classifier = RuleClassifier(topics_path, topic="labor_care_poverty")
    labor: list[tuple[ArticleRecord, float]] = []
    for item in articles:
        if item.source in BROADCAST_SOURCES or is_opinion(item):
            continue
        if item.canonical_url in disability_urls or item.classification in {
            Classification.RELEVANT,
            Classification.REVIEW,
        }:
            if "최중증 발달장애인 통합돌봄" in _article_text(item):
                editorial_notes.append(f"II절 제외·I절 배치 — {item.title}")
            continue
        result = labor_classifier.classify(
            title=item.title, summary=item.summary, section=item.section
        )
        if result.classification not in {Classification.RELEVANT, Classification.REVIEW}:
            continue
        if "보양식 세트" in _article_text(item):
            editorial_notes.append(f"II절 제외 — {item.title}: 보양식 제공 여부 중심의 단발성 사안")
            continue
        labor.append((item, result.topic_score))
    labor.sort(key=lambda pair: pair[1], reverse=True)

    broadcast = [
        item
        for item in articles
        if item.source in BROADCAST_SOURCES
        and item.classification in {Classification.RELEVANT, Classification.REVIEW}
    ]

    sections = [
        BriefingSection("I. 장애정책·장애인운동", disability_issues),
        BriefingSection(
            "II. 노동·돌봄·빈곤",
            cluster_issues([item for item, _score in labor], history=history, max_issues=10),
        ),
        BriefingSection(
            "III. 방송 뉴스 중 장애 주제",
            cluster_issues(broadcast, history=history, max_issues=10),
        ),
    ]
    opinions = select_opinions(articles)
    opinion_issues = cluster_issues(opinions, history=history, max_issues=12)
    if opinion_issues:
        sections.append(BriefingSection("IV. 주요 칼럼", opinion_issues))
    else:
        editorial_notes.append("IV절 생략 — 지정 칼럼과 지정 7개 매체 장애 관련 칼럼 없음")

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


def _briefing_issue(cluster: list[ArticleRecord], history: list[ArticleRecord]) -> BriefingIssue:
    lead = max(cluster, key=lambda item: (_disability_priority(item), item.topic_score))
    text = " ".join(_article_text(item) for item in cluster)
    articles = _sort_cluster_articles(cluster)
    previous = previous_coverage_for(articles, history)
    disability_context = any(
        article.classification in {Classification.RELEVANT, Classification.REVIEW}
        for article in cluster
    )
    linked_crpd = crpd_articles(text) if disability_context else []
    return BriefingIssue(
        title=lead.title,
        articles=articles,
        summary=summarize_articles(articles),
        tone_analysis=analyze_tone(articles),
        previous_coverage=previous,
        references=reference_rows(
            articles,
            previous,
            linked_crpd,
            disability_context=disability_context,
        ),
        crpd_articles=linked_crpd,
    )


def is_opinion(article: ArticleRecord) -> bool:
    # 본문·요약의 '사설구급차' 같은 단어를 '사설' 표지로 오인하지
    # 않도록 편집 유형을 드러내는 제목·섹션·URL만 판정한다.
    haystack = " ".join(value for value in (article.title, article.section) if value).lower()
    path = urlsplit(article.canonical_url).path.lower()
    return any(term.lower() in haystack for term in OPINION_TERMS) or any(
        marker in path for marker in OPINION_PATHS
    )


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
            and article.classification in {Classification.RELEVANT, Classification.REVIEW}
        )
        if mandatory or disability_column:
            selected.append(article)
    selected.sort(key=lambda item: item.published_at or item.first_seen_at, reverse=True)
    return selected


def summarize_articles(articles: list[ArticleRecord]) -> str:
    summaries: list[str] = []
    for article in articles[:3]:
        cleaned = _clean_summary(article.summary)
        if cleaned and not any(_similar_text(cleaned, existing) for existing in summaries):
            summaries.append(cleaned)
    if not summaries:
        titles = "; ".join(article.title for article in articles[:3])
        return f"공개된 제목과 메타데이터에 따르면 {titles}에 관한 사안이 제기되었다."
    if len(summaries) == 1:
        return summaries[0]
    outlets = ", ".join(
        dict.fromkeys(SOURCE_LABELS.get(item.source, item.source) for item in articles)
    )
    joined = " ".join(summaries[:2])
    return f"{outlets}의 공개 보도를 종합하면 {joined}"


def analyze_tone(articles: list[ArticleRecord]) -> str:
    if len(articles) == 1:
        article = articles[0]
        label = SOURCE_LABELS.get(article.source, article.source)
        return (
            f"{label}{_korean_particle(label, '은', '는')} {_tone_focus(article)} "
            "단일 매체 보도이므로 당사자·단체와 "
            "정부·지자체·사용자 측의 원자료와 후속 입장을 함께 확인할 필요가 있다."
        )
    descriptions = [
        f"{(label := SOURCE_LABELS.get(article.source, article.source))}"
        f"{_korean_particle(label, '은', '는')} {_tone_focus(article).rstrip('.')}"
        for article in articles
    ]
    return " ".join(f"{description}." for description in descriptions) + (
        " 매체별 강조점의 차이를 사실관계의 변화와 논조의 차이로 나누어 읽어야 한다."
    )


def previous_coverage_for(
    articles: list[ArticleRecord], history: list[ArticleRecord]
) -> list[PreviousCoverage]:
    text = " ".join(_article_text(article) for article in articles)
    result: list[PreviousCoverage] = []
    for triggers, published, label, url, comparison in KNOWN_PREVIOUS_COVERAGE:
        if all(trigger in text for trigger in triggers):
            result.append(PreviousCoverage(published, label, url, comparison))

    tokens = set().union(*(issue_tokens(article) for article in articles))
    strong_tokens = {term.lower() for term in STRONG_PREVIOUS_CONCEPTS} & tokens
    current_urls = {article.canonical_url for article in articles}
    candidates: list[tuple[int, datetime, ArticleRecord]] = []
    for article in history:
        if article.canonical_url in current_urls or is_opinion(article):
            continue
        overlap = tokens & issue_tokens(article)
        if not overlap & strong_tokens and len(overlap) < 4:
            continue
        candidates.append((len(overlap), article.published_at or article.first_seen_at, article))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    known_urls = {item.url for item in result}
    for _score, published, article in candidates:
        if article.canonical_url in known_urls:
            continue
        result.append(
            PreviousCoverage(
                published.astimezone(KST).date().isoformat(),
                article.title,
                article.canonical_url,
                "당일 보도와 대조하여 요구·정책·행정조치가 실제로 달라졌는지 확인한다.",
            )
        )
        known_urls.add(article.canonical_url)
        if len(result) >= 3:
            break
    return result[:3]


def reference_rows(
    articles: list[ArticleRecord],
    previous: list[PreviousCoverage],
    linked_crpd: list[str],
    *,
    disability_context: bool,
) -> list[BriefingReference]:
    text = " ".join(_article_text(article) for article in articles)
    rows: list[BriefingReference] = []
    if linked_crpd:
        rows.append(
            BriefingReference(
                "국제 규범",
                f"{CRPD_FULL_TEXT[0]} — {', '.join(linked_crpd)}",
                CRPD_FULL_TEXT[1],
                "장애인의 권리를 임의적 복지사업이 아니라 국가와 지방정부의 이행의무로 확인한다.",
            )
        )
        seen_comments: set[str] = set()
        for article_name in linked_crpd:
            article_number = article_name.split(" ", 1)[0]
            comment = GENERAL_COMMENTS.get(article_number)
            if not comment or comment[1] in seen_comments:
                continue
            rows.append(
                BriefingReference(
                    "국제 규범",
                    comment[0],
                    comment[1],
                    "유엔장애인권리위원회의 권위 있는 협약 해석기준으로 적용한다.",
                )
            )
            seen_comments.add(comment[1])
        if any(number in text for number in ("탈시설", "집단 전원", "전원 자립", "색동원")):
            rows.append(
                BriefingReference(
                    "국제 규범",
                    DEINSTITUTIONALIZATION_GUIDELINES[0],
                    DEINSTITUTIONALIZATION_GUIDELINES[1],
                    "시설 간 이동이 아니라 당사자의 선택과 지역사회 지원을 기준으로 삼는다.",
                )
            )
    law_references = LAW_REFERENCES
    research_references = RESEARCH_REFERENCES
    if disability_context:
        law_references = {
            key: value
            for key, value in LAW_REFERENCES.items()
            if key not in LABOR_ONLY_REFERENCE_KEYS
        }
        research_references = {
            key: value
            for key, value in RESEARCH_REFERENCES.items()
            if key not in LABOR_ONLY_REFERENCE_KEYS
        }
    else:
        rows.extend(_labor_norms(text))
    rows.extend(_mapped_references("현행 제도", text, law_references))
    rows.extend(_mapped_references("참고 연구·문서", text, research_references, limit=2))
    rows.extend(_organization_positions(articles))
    rows.extend(
        BriefingReference(
            "이전 주요 핵심 기사",
            item.label,
            item.url,
            item.comparison,
        )
        for item in previous
    )
    return _unique_references(rows)


def crpd_articles(text: str) -> list[str]:
    return [article for article, terms in CRPD_TERMS.items() if any(term in text for term in terms)]


def build_overview(start: datetime, end: datetime, sections: list[BriefingSection]) -> str:
    by_title = {section.title: section for section in sections}
    sentences = [
        f"이번 브리핑은 {kst_display(start)}부터 {kst_display(end)}까지 보도된 사안 가운데 "
        "장애인의 시민권과 노동자·빈곤 당사자의 생명·생존권에 영향을 미치는 내용을 "
        "정리하였다."
    ]
    disability = by_title.get("I. 장애정책·장애인운동")
    if disability and disability.issues:
        sentences.append(
            "장애정책·장애인운동에서는 "
            + _issue_titles(disability.issues, 4)
            + " 등을 중심으로 지역사회에서 살아갈 권리와 공적 책임의 이행 여부를 살폈다."
        )
    labor = by_title.get("II. 노동·돌봄·빈곤")
    if labor and labor.issues:
        sentences.append(
            "노동·돌봄·빈곤 부문에서는 "
            + _issue_titles(labor.issues, 4)
            + " 등을 통해 원청·사용자와 국가의 책임을 점검하였다."
        )
    broadcast = by_title.get("III. 방송 뉴스 중 장애 주제")
    if broadcast and broadcast.issues:
        sentences.append(
            "방송 뉴스에서는 " + _issue_titles(broadcast.issues, 2) + " 등 관련 보도를 확인하였다."
        )
    columns = by_title.get("IV. 주요 칼럼")
    if columns and columns.issues:
        sentences.append(
            "주요 칼럼으로는 " + _issue_titles(columns.issues, 3) + "을 함께 소개한다."
        )
    sentences.append(
        "각 사안은 시혜나 개인의 불운이 아니라 자본과 국가·지방정부가 부담해야 할 "
        "구조적 책임의 문제로 읽어야 한다."
    )
    return " ".join(sentences[:8])


def build_telegram_summary(sections: list[BriefingSection]) -> str:
    by_title = {section.title: section for section in sections}
    sentences: list[str] = []
    disability = by_title.get("I. 장애정책·장애인운동")
    if disability and disability.issues:
        sentences.append(
            "장애 의제에서는 "
            + _issue_titles(disability.issues, 4)
            + "을 주요 후속 감시 대상으로 정리하였다."
        )
    labor = by_title.get("II. 노동·돌봄·빈곤")
    if labor and labor.issues:
        labor_titles = _issue_titles(labor.issues, 3)
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
    return " ".join(sentences[:4])


def render_briefing_markdown(document: BriefingDocument, *, crpd_url: str | None) -> str:
    del crpd_url
    lines = [
        f"# 일간 장애정책·노동 뉴스 브리핑 ({document.report_date})",
        "",
        document.overview,
        "",
    ]
    for section in document.sections:
        if section.title == "IV. 주요 칼럼" and not section.issues:
            continue
        lines.extend([f"# {section.title}", ""])
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
            lines.extend(["", "### 기사 요약", "", issue.summary, ""])
            lines.extend(["### 보도 논조", "", issue.tone_analysis, ""])
            if issue.previous_coverage:
                lines.extend(["### 이전 보도 참고", ""])
                lines.extend(
                    [
                        "| 시점 | 비교 자료 | 주요 내용·비교점 |",
                        "|---|---|---|",
                    ]
                )
                for item in issue.previous_coverage:
                    label = _markdown_table_text(item.label)
                    material = f"[{label}]({item.url})" if item.url else label
                    lines.append(
                        f"| {item.published} | {material} | "
                        f"{_markdown_table_text(item.comparison)} |"
                    )
                lines.append("")
            lines.extend(["<details>", "<summary>추가 자료 · 더 알아보기</summary>", ""])
            lines.extend(
                [
                    "| 범주 | 자료 | 확인 쟁점 |",
                    "|---|---|---|",
                ]
            )
            for reference in issue.references:
                label = _markdown_table_text(reference.label)
                material = f"[{label}]({reference.url})" if reference.url else label
                lines.append(
                    f"| {reference.category} | {material} | "
                    f"{_markdown_table_text(reference.note)} |"
                )
            if not issue.references:
                lines.append("| 참고 자료 | 확인된 추가 자료 없음 | 후속 조사 필요 |")
            lines.extend(["", "</details>", ""])
    return "\n".join(lines)


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


def _clean_summary(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"^【[^】]+】\s*", "", value).strip()
    cleaned = re.sub(r"^\[[^\]]{1,40}\]\s*", "", cleaned).strip()
    if "지역사 채널의 동영상 링크" in cleaned and "무단 전재" in cleaned:
        return None
    cleaned = cleaned.replace("...", "…")
    cleaned = re.sub(r"""(?<=[.!?])(?=[가-힣A-Z0-9"'])""", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    unique: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or any(_similar_text(sentence, item) for item in unique):
            continue
        unique.append(sentence)
        if len(unique) >= 2:
            break
    excerpt = " ".join(unique)
    return short_text(excerpt or cleaned, 420)


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


def _mapped_references(
    category: str,
    text: str,
    mapping: dict[str, tuple[str, str]],
    *,
    limit: int = 3,
) -> list[BriefingReference]:
    rows: list[BriefingReference] = []
    for trigger, (label, url) in mapping.items():
        if trigger not in text:
            continue
        rows.append(
            BriefingReference(
                category,
                label,
                url,
                "기사의 주장과 정책·법적 의무를 원자료에서 대조한다.",
            )
        )
        if len(rows) >= limit:
            break
    return rows


def _labor_norms(text: str) -> list[BriefingReference]:
    rows: list[BriefingReference] = []
    if any(term in text for term in ("산업재해", "산재", "사망", "끼임")):
        rows.append(
            BriefingReference(
                "국제 규범",
                "ILO 산업안전보건협약 제155호",
                "https://normlex.ilo.org/dyn/nrmlx_en/f?p=NORMLEXPUB:12100:0::NO::P12100_ILO_CODE:C155",
                "예방의무와 노동자의 생명·건강 보호를 도급계약과 기업 이윤보다 우선한다.",
            )
        )
    if any(term in text for term in ("노조", "파업", "교섭")):
        rows.append(
            BriefingReference(
                "국제 규범",
                "ILO 결사의 자유 협약 제87호",
                "https://normlex.ilo.org/dyn/nrmlx_en/f?p=NORMLEXPUB:12100:0::NO::P12100_ILO_CODE:C087",
                "노동자의 조직·교섭·집단행동을 기본권의 관점에서 확인한다.",
            )
        )
    if any(term in text for term in ("최저임금", "빈곤", "생계", "돌봄")):
        rows.append(
            BriefingReference(
                "국제 규범",
                "유엔 경제적·사회적·문화적 권리규약",
                "https://www.ohchr.org/en/instruments-mechanisms/instruments/international-covenant-economic-social-and-cultural-rights",
                "공정한 노동조건·사회보장·적절한 생활수준을 결합해 해석한다.",
            )
        )
    return rows


def _organization_positions(articles: list[ArticleRecord]) -> list[BriefingReference]:
    rows: list[BriefingReference] = []
    for article in articles:
        text = _article_text(article)
        if not any(
            term in text for term in ("공대위", "장차연", "장애인단체", "노조", "노동조합", "성명")
        ):
            continue
        rows.append(
            BriefingReference(
                "관련 단체 입장",
                f"{article.title}에 담긴 당사자·관련 단체 입장",
                article.canonical_url,
                "기사에 인용된 요구를 확인하되 가능하면 단체의 성명·요구안 원문과 다시 대조한다.",
            )
        )
    return rows[:2]


def _unique_references(rows: list[BriefingReference]) -> list[BriefingReference]:
    unique: list[BriefingReference] = []
    seen: set[tuple[str, str | None]] = set()
    for row in rows:
        key = row.label, row.url
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


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


def _issue_titles(issues: list[BriefingIssue], limit: int) -> str:
    labels = []
    for issue in issues[:limit]:
        title = re.sub(r"\s*\(\d{4}\.\d{2}\.\d{2}/[^)]*\)$", "", issue.title)
        labels.append(short_text(title, 65) or title)
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
