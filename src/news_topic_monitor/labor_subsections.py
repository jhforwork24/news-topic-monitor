from __future__ import annotations

from .models import LaborSubsection

# II절("노동·돌봄·빈곤·소수자")은 돌봄 → 빈곤 → 소수자 → 노동 순으로 배치하고, 하위 주제별
# 상한을 둔다(2026-10-31 사용자 결정: 소수자 하위 주제 신설, 상한 3·3·2·2, 전체 8).
# 상한은 최대치이며 목표가 아니다. 발행할 만한 이슈가 없으면 하위 주제나 II절 전체를 0개로
# 발행할 수 있다.
LABOR_SUBSECTION_ORDER: tuple[LaborSubsection, ...] = (
    LaborSubsection.CARE,
    LaborSubsection.POVERTY,
    LaborSubsection.MINORITY,
    LaborSubsection.LABOR,
)
LABOR_SUBSECTION_LABELS = {
    LaborSubsection.CARE: "돌봄",
    LaborSubsection.POVERTY: "빈곤",
    LaborSubsection.MINORITY: "소수자",
    LaborSubsection.LABOR: "노동",
}
LABOR_SUBSECTION_MAX_ISSUES = {
    LaborSubsection.CARE: 3,
    LaborSubsection.POVERTY: 3,
    LaborSubsection.MINORITY: 2,
    LaborSubsection.LABOR: 2,
}
LABOR_SECTION_MAX_ISSUES = 8

# 편집 계획이 subsection을 지정하지 않았을 때(또는 결정론적 경로에서) 이슈의 제목·키워드·
# 요약·논조를 config/topics.yml의 labor_care_poverty 용어 가운데 돌봄·빈곤·소수자 쪽과 대조해
# 판정한다. 가장 많이 걸린 쪽을 고르고 동점이면 돌봄 > 빈곤 > 소수자 순으로 정한다. 어느 쪽도
# 안 걸리면 이 섹션의 기본값인 노동으로 둔다. 장애 사안은 I절에서만 다룬다.
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

# 이주민·이주노동자·난민, 성소수자, 차별금지법·혐오·인권위, 여성·성평등. 이주노동자의 노동조건
# 기사도 소수자로 분류한다(2026-10-31 사용자 결정).
MINORITY_SUBSECTION_TERMS = (
    "소수자",
    "이주민",
    "이주노동자",
    "이주노동",
    "외국인 노동자",
    "외국인노동자",
    "미등록 이주",
    "고용허가제",
    "계절근로",
    "난민",
    "성소수자",
    "트랜스젠더",
    "동성애",
    "동성혼",
    "동성 부부",
    "동성 커플",
    "퀴어",
    "차별금지법",
    "평등법",
    "혐오",
    "인권위",
    "국가인권위원회",
    "성평등",
    "여성가족부",
    "성차별",
    "성별 임금",
    "젠더폭력",
    "교제폭력",
    "디지털 성범죄",
    "여성노동",
)


def classify_labor_subsection(*texts: str | None) -> LaborSubsection:
    haystack = " ".join(value for value in texts if value)
    hits = {
        LaborSubsection.CARE: sum(1 for term in CARE_SUBSECTION_TERMS if term in haystack),
        LaborSubsection.POVERTY: sum(1 for term in POVERTY_SUBSECTION_TERMS if term in haystack),
        LaborSubsection.MINORITY: sum(1 for term in MINORITY_SUBSECTION_TERMS if term in haystack),
    }
    best = max(hits.values())
    if not best:
        return LaborSubsection.LABOR
    # dict는 삽입 순서(돌봄 > 빈곤 > 소수자)를 지키므로 동점이면 앞쪽이 이긴다.
    return next(subsection for subsection, count in hits.items() if count == best)


def labor_subsection_rank(subsection: LaborSubsection) -> int:
    return LABOR_SUBSECTION_ORDER.index(subsection)
