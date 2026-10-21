from __future__ import annotations

import re

from .briefing import (
    PREVIOUS_COVERAGE_MAX_ITEMS,
    BriefingDocument,
    editorial_opinion_allowed,
    labor_editorial_exclusion,
    render_briefing_markdown,
)
from .models import ArticleRecord, Classification
from .sources import SOURCE_LABELS
from .utils import stable_article_key

FORBIDDEN_SUMMARY_MARKERS = ('"', "'", "“", "”", "\u2018", "\u2019", "...", "…")
FORBIDDEN_TONE_LABELS = ("(진보)", "(보수)", "(전문지)")
# "필자"는 칼럼·사설(III절)의 서술자를 가리킬 때만 쓴다. I·II절 보도는 취재기사이므로
# 서술 주체가 기자 개인이 아니라 매체다 — 이름을 밝히지 않는 게 아니라 애초에 틀린 지칭이다.
FORBIDDEN_AUTHOR_TERM = "필자"
# 논조(tone_analysis) 문장 수 규칙. 편집 지침(docs/claude-editorial-instructions.md)·감사 과제
# (docs/claude-auditor-task.md)·편집 프롬프트(editorial.py)가 같은 숫자를 말하며,
# tests/test_rule_doc_sync.py가 셋의 일치를 고정한다. 값을 바꾸면 세 곳과 시험을 함께 고친다.
TONE_SINGLE_MAX_SENTENCES = 1
TONE_MULTI_MIN_SENTENCES = 1
TONE_MULTI_MAX_SENTENCES = 4
# 이슈 요약(summary)은 매체를 언급하지 않는 사안 요약이다. 매체별 보도 방식은 tone_analysis의
# 몫이므로, I·II절 요약에 언론사 이름이 있으면 역할이 섞인 것으로 보고 거부한다. III절은
# 필자의 주장을 요약하므로 이 검사에서 제외한다.
OUTLET_NAMES = tuple(sorted(set(SOURCE_LABELS.values()), key=len, reverse=True))
# Flags 해요체/합쇼체 sentence endings (e.g. "...입니다.", "...습니다.", "...하죠.").
# "니다" is excluded when preceded by "아" so the plain-register negative copula
# "아니다" (e.g. "확정된 금액은 아니다.") isn't mistaken for the "-습니다"/"-입니다"
# formal ending it happens to share a two-syllable substring with.
_NON_NEUTRAL_ENDING = re.compile(r"(?:요|죠)\.$|(?<!아)니다\.$")


class BriefingValidationError(ValueError):
    pass


def issue_text_errors(
    *,
    label: str,
    title: str,
    summary: str,
    tone_analysis: str,
    keyword: str,
    article_count: int,
    opinion_section: bool,
    check_keyword: bool = True,
) -> list[str]:
    """Mechanical text rules for one issue, shared by publish validation and the draft preflight."""

    errors: list[str] = []
    if any(marker in summary for marker in FORBIDDEN_SUMMARY_MARKERS):
        errors.append(f"{label}: 요약에 인용·말줄임 표지가 있음")
    if not summary.endswith("."):
        errors.append(f"{label}: 요약이 완성형 문장이 아님")
    if _NON_NEUTRAL_ENDING.search(summary):
        errors.append(f"{label}: 요약이 중립적 서술체가 아님")

    tone_sentences = _sentence_count(tone_analysis)
    if article_count == 1 and tone_analysis.strip() and tone_sentences != TONE_SINGLE_MAX_SENTENCES:
        errors.append(f"{label}: 단일 보도 논조가 한 문장이 아님")
    if article_count > 1 and not (
        TONE_MULTI_MIN_SENTENCES <= tone_sentences <= TONE_MULTI_MAX_SENTENCES
    ):
        errors.append(
            f"{label}: 복수 보도 논조가 "
            f"{TONE_MULTI_MIN_SENTENCES}~{TONE_MULTI_MAX_SENTENCES}문장이 아님"
        )
    if any(item in tone_analysis for item in FORBIDDEN_TONE_LABELS):
        errors.append(f"{label}: 논조 비교에 매체 진영 라벨이 노출됨")
    if not opinion_section:
        named = [name for name in OUTLET_NAMES if name in summary]
        if named:
            outlets = ", ".join(named)
            errors.append(
                f"{label}: 이슈 요약에 언론사({outlets})를 "
                "언급함 — 매체별 보도 방식은 보도 논조에 쓴다"
            )
        if FORBIDDEN_AUTHOR_TERM in (summary + tone_analysis):
            errors.append(f"{label}: 칼럼이 아닌데 '필자'로 지칭함")
    if check_keyword:
        stripped = keyword.strip()
        if not stripped:
            errors.append(f"{label}: 키워드 요약이 비어 있음")
        elif stripped == title.strip():
            errors.append(f"{label}: 키워드 요약이 제목을 그대로 인용함")
    return errors


def validate_briefing(document: BriefingDocument) -> None:
    errors: list[str] = []
    editorial_ids = set(document.editorially_selected_ids)
    rendered_article_ids: set[str] = set()
    for section in document.sections:
        for issue in section.issues:
            rendered_article_ids.update(_article_id(article) for article in issue.articles)
            if len(issue.previous_coverage) > PREVIOUS_COVERAGE_MAX_ITEMS:
                errors.append(
                    f"{section.title} / {issue.title}: "
                    f"이전 보도가 {PREVIOUS_COVERAGE_MAX_ITEMS}개를 초과함"
                )
            errors.extend(
                issue_text_errors(
                    label=f"{section.title} / {issue.title}",
                    title=issue.title,
                    summary=issue.summary,
                    tone_analysis=issue.tone_analysis,
                    keyword=issue.keyword,
                    article_count=len(issue.articles),
                    opinion_section=section.title.startswith("III."),
                    check_keyword=bool(document.editorially_selected_ids),
                )
            )

            if section.title.startswith("I."):
                invalid = [
                    article.title
                    for article in issue.articles
                    if article.classification != Classification.RELEVANT
                    and _article_id(article) not in editorial_ids
                ]
                if invalid:
                    errors.append(f"{section.title} / {issue.title}: 자동확정되지 않은 기사 포함")

            if section.title.startswith("II.") and any(
                labor_editorial_exclusion(article) for article in issue.articles
            ):
                errors.append(f"{section.title} / {issue.title}: 사진·연예·스포츠 보도 포함")

            if section.title.startswith("III.") and any(
                not editorial_opinion_allowed(article) for article in issue.articles
            ):
                errors.append(f"{section.title} / {issue.title}: 허용 범위 밖의 칼럼이 포함됨")

    if len(document.editorially_selected_ids) != len(editorial_ids):
        errors.append("GPT 편집 선정 ID가 중복됨")
    if editorial_ids and editorial_ids != rendered_article_ids:
        errors.append("GPT 편집 선정 ID와 렌더링된 기사 ID가 일치하지 않음")

    rendered = render_briefing_markdown(document, crpd_url=None)
    forbidden_output = {
        "KST": "시간대 약칭 KST가 남아 있음",
        "| 언론사 | 기사 | 발행 |": "주요 언론 보도가 표로 출력됨",
        "### 기사 요약": "기사 요약이 별도 항목으로 출력됨",
        "### 보도 논조": "보도 논조가 별도 항목으로 출력됨",
        "### 동일 주제 이전 보도": "이전 보도가 토글 밖에 출력됨",
    }
    for marker, message in forbidden_output.items():
        if marker in rendered:
            errors.append(message)

    if errors:
        raise BriefingValidationError("브리핑 발행 검증 실패: " + "; ".join(errors))


def _sentence_count(value: str) -> int:
    if not value.strip():
        return 0
    return len(re.findall(r"[.!?](?=\s|$)", value))


def _article_id(article: ArticleRecord) -> str:
    return stable_article_key(
        article.source,
        article.canonical_url,
        article.article_id,
        article.title,
        article.published_at,
    )
