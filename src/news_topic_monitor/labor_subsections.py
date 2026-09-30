from __future__ import annotations

from .models import LaborSubsection

# II절("노동·돌봄·빈곤")은 돌봄 → 빈곤 → 노동 순으로 배치하고, 하위 주제별 상한을 둔다.
# 상한은 최대치이며 목표가 아니다. 발행할 만한 이슈가 없으면 하위 주제나 II절 전체를 0개로
# 발행할 수 있다.
LABOR_SUBSECTION_ORDER: tuple[LaborSubsection, ...] = (
    LaborSubsection.CARE,
    LaborSubsection.POVERTY,
    LaborSubsection.LABOR,
)
LABOR_SUBSECTION_LABELS = {
    LaborSubsection.CARE: "돌봄",
    LaborSubsection.POVERTY: "빈곤",
    LaborSubsection.LABOR: "노동",
}
LABOR_SUBSECTION_MAX_ISSUES = {
    LaborSubsection.CARE: 4,
    LaborSubsection.POVERTY: 3,
    LaborSubsection.LABOR: 3,
}
LABOR_SECTION_MAX_ISSUES = 7

# 편집 계획이 subsection을 지정하지 않았을 때(또는 결정론적 경로에서) 이슈의 제목·키워드·
# 요약·논조를 config/topics.yml의 labor_care_poverty 용어 가운데 돌봄·빈곤 쪽과 대조해 판정한다.
# 두 쪽 다 안 걸리거나 동점이면 이 섹션의 기본값인 노동으로 둔다.
CARE_SUBSECTION_TERMS = (
    "돌봄노동",
    "돌봄서비스",
    "돌봄",
    "요양보호사",
    "활동지원사",
    "활동지원",
    "간병",
    "공공돌봄",
    "노인장기요양보험",
    "장기요양",
    "사회서비스원",
)
POVERTY_SUBSECTION_TERMS = (
    "빈곤",
    "생계급여",
    "생계",
    "기초생활보장",
    "기초생활",
    "노숙",
    "부양의무자",
    "차상위계층",
    "자활사업",
    "자활근로",
    "복지사각지대",
    "위기가구",
    "수급자",
    "주거급여",
    "주거권",
    "소득보장",
    "긴급복지지원",
    "기초연금",
    "근로장려금",
)


def classify_labor_subsection(*texts: str | None) -> LaborSubsection:
    haystack = " ".join(value for value in texts if value)
    care_hits = sum(1 for term in CARE_SUBSECTION_TERMS if term in haystack)
    poverty_hits = sum(1 for term in POVERTY_SUBSECTION_TERMS if term in haystack)
    if care_hits and care_hits >= poverty_hits:
        return LaborSubsection.CARE
    if poverty_hits:
        return LaborSubsection.POVERTY
    return LaborSubsection.LABOR


def labor_subsection_rank(subsection: LaborSubsection) -> int:
    return LABOR_SUBSECTION_ORDER.index(subsection)
