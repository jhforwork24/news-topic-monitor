from __future__ import annotations

import re

from .briefing import (
    BriefingDocument,
    editorial_opinion_allowed,
    labor_editorial_exclusion,
    render_briefing_markdown,
)
from .models import ArticleRecord, Classification
from .utils import stable_article_key

FORBIDDEN_SUMMARY_MARKERS = ('"', "'", "“", "”", "\u2018", "\u2019", "...", "…")
FORBIDDEN_TONE_LABELS = ("(진보)", "(보수)", "(전문지)")
# "필자"는 칼럼·사설(III절)의 서술자를 가리킬 때만 쓴다. I·II절 보도는 취재기사이므로
# 서술 주체가 기자 개인이 아니라 매체다 — 이름을 밝히지 않는 게 아니라 애초에 틀린 지칭이다.
FORBIDDEN_AUTHOR_TERM = "필자"
# Flags 해요체/합쇼체 sentence endings (e.g. "...입니다.", "...습니다.", "...하죠.").
# "니다" is excluded when preceded by "아" so the plain-register negative copula
# "아니다" (e.g. "확정된 금액은 아니다.") isn't mistaken for the "-습니다"/"-입니다"
# formal ending it happens to share a two-syllable substring with.
_NON_NEUTRAL_ENDING = re.compile(r"(?:요|죠)\.$|(?<!아)니다\.$")


class BriefingValidationError(ValueError):
    pass


def validate_briefing(document: BriefingDocument) -> None:
    errors: list[str] = []
    editorial_ids = set(document.editorially_selected_ids)
    rendered_article_ids: set[str] = set()
    for section in document.sections:
        for issue in section.issues:
            rendered_article_ids.update(_article_id(article) for article in issue.articles)
            if len(issue.previous_coverage) > 3:
                errors.append(f"{section.title} / {issue.title}: 이전 보도가 3개를 초과함")
            if any(marker in issue.summary for marker in FORBIDDEN_SUMMARY_MARKERS):
                errors.append(f"{section.title} / {issue.title}: 요약에 인용·말줄임 표지가 있음")
            if not issue.summary.endswith("."):
                errors.append(f"{section.title} / {issue.title}: 요약이 완성형 문장이 아님")
            if _NON_NEUTRAL_ENDING.search(issue.summary):
                errors.append(f"{section.title} / {issue.title}: 요약이 중립적 서술체가 아님")

            tone_sentences = _sentence_count(issue.tone_analysis)
            if len(issue.articles) == 1 and issue.tone_analysis.strip() and tone_sentences != 1:
                errors.append(f"{section.title} / {issue.title}: 단일 보도 논조가 한 문장이 아님")
            if len(issue.articles) > 1 and not 1 <= tone_sentences <= 4:
                errors.append(f"{section.title} / {issue.title}: 복수 보도 논조가 1~4문장이 아님")
            if any(label in issue.tone_analysis for label in FORBIDDEN_TONE_LABELS):
                errors.append(
                    f"{section.title} / {issue.title}: 논조 비교에 매체 진영 라벨이 노출됨"
                )
            if not section.title.startswith("III.") and FORBIDDEN_AUTHOR_TERM in (
                issue.summary + issue.tone_analysis
            ):
                errors.append(f"{section.title} / {issue.title}: 칼럼이 아닌데 '필자'로 지칭함")

            if document.editorially_selected_ids:
                keyword = issue.keyword.strip()
                if not keyword:
                    errors.append(f"{section.title} / {issue.title}: 키워드 요약이 비어 있음")
                elif keyword == issue.title.strip():
                    errors.append(
                        f"{section.title} / {issue.title}: 키워드 요약이 제목을 그대로 인용함"
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
