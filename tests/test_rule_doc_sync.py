"""편집·감사 지침 문서, 편집 프롬프트, 발행 검증 코드가 같은 규칙 숫자를 말하는지 고정한다.

2026-10-07 다른 세션이 발행 검증의 문장 수 규칙만 바꾸고 지침 문서를 그대로 둔 채 병합해,
지침대로 쓴 초안이 다음 날 거부될 뻔했다. 검증 규칙의 숫자는 코드 상수가 정본이고, 문서와
프롬프트가 그 숫자를 그대로 옮겨 적고 있어야 한다. 규칙을 바꾸면 이 시험이 어긋난 문서를
지목한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from news_topic_monitor.briefing_validation import (
    TONE_MULTI_MAX_SENTENCES,
    TONE_MULTI_MIN_SENTENCES,
    TONE_SINGLE_MAX_SENTENCES,
)
from news_topic_monitor.editorial import (
    MAX_EXCLUSIONS,
    SECTION_MAX_ISSUES,
    EditorialSection,
    _planning_prompt,
)
from news_topic_monitor.labor_subsections import (
    LABOR_SECTION_MAX_ISSUES,
    LABOR_SUBSECTION_MAX_ISSUES,
    LaborSubsection,
)

ROOT = Path(__file__).resolve().parents[1]


def _doc(name: str) -> str:
    # 줄바꿈 위치와 들여쓰기는 규칙이 아니므로 공백을 하나로 모아 비교한다.
    return re.sub(r"\s+", " ", (ROOT / "docs" / name).read_text(encoding="utf-8"))


EDITORIAL = _doc("claude-editorial-instructions.md")
AUDITOR = _doc("claude-auditor-task.md")
PROMPT = re.sub(r"\s+", " ", _planning_prompt())


def _tone_rule() -> str:
    return (
        f"단일 보도 0~{TONE_SINGLE_MAX_SENTENCES}문장, "
        f"복수 보도 {TONE_MULTI_MIN_SENTENCES}~{TONE_MULTI_MAX_SENTENCES}문장"
    )


def test_editorial_instructions_state_the_tone_sentence_limits_of_the_validator() -> None:
    assert _tone_rule() in EDITORIAL


def test_planning_prompt_states_the_tone_sentence_limits_of_the_validator() -> None:
    expected = (
        f"단일 보도면 0~{TONE_SINGLE_MAX_SENTENCES}문장, "
        f"복수 보도면 {TONE_MULTI_MIN_SENTENCES}~{TONE_MULTI_MAX_SENTENCES}문장"
    )
    assert expected in PROMPT


def test_auditor_task_states_the_tone_sentence_limits_of_the_validator() -> None:
    # 검증은 단일 보도의 논조가 비어 있는 것을 허용한다(편집 지침의 "0~1문장").
    expected = (
        f"단일 보도는 비어 있거나 정확히 {TONE_SINGLE_MAX_SENTENCES}문장, "
        f"복수 보도는 {TONE_MULTI_MIN_SENTENCES}~{TONE_MULTI_MAX_SENTENCES}문장"
    )
    assert expected in AUDITOR


def test_editorial_instructions_state_the_section_issue_caps() -> None:
    disability = SECTION_MAX_ISSUES[EditorialSection.DISABILITY]
    opinion = SECTION_MAX_ISSUES[EditorialSection.OPINION]
    assert disability == opinion  # 지침은 두 섹션을 한 문장으로 묶어 말한다.
    assert f"각각 최대 {disability}개 이슈" in EDITORIAL
    assert f"`labor` 섹션(II절)은 최대 {LABOR_SECTION_MAX_ISSUES}개 이슈" in EDITORIAL


def test_editorial_instructions_and_auditor_state_the_labor_subsection_caps() -> None:
    care = LABOR_SUBSECTION_MAX_ISSUES[LaborSubsection.CARE]
    poverty = LABOR_SUBSECTION_MAX_ISSUES[LaborSubsection.POVERTY]
    labor = LABOR_SUBSECTION_MAX_ISSUES[LaborSubsection.LABOR]
    total = LABOR_SECTION_MAX_ISSUES
    assert (
        f"돌봄(`care`) 0~{care}개, 빈곤(`poverty`) 0~{poverty}개, "
        f"노동(`labor`) 0~{labor}개, II절 전체 0~{total}개"
    ) in EDITORIAL
    assert f"(돌봄 ≤{care}, 빈곤 ≤{poverty}, 노동 ≤{labor}, 전체 ≤{total})" in AUDITOR


def test_exclusion_cap_matches_the_instructions_and_the_prompt() -> None:
    assert f"exclusions는 최대 {MAX_EXCLUSIONS}개" in EDITORIAL
    assert f"최대 {MAX_EXCLUSIONS}개까지 기록한다" in PROMPT
