from __future__ import annotations

from dataclasses import dataclass

from .models import AuditSeverity

SOURCE_LABELS = {
    "chosun": "조선일보",
    "joongang": "중앙일보",
    "donga": "동아일보",
    "hani": "한겨레",
    "khan": "경향신문",
    "ohmynews": "오마이뉴스",
    "pressian": "프레시안",
    "newscham": "참세상",
    "labortoday": "매일노동뉴스",
    "mediaus": "미디어스",
    "beminor": "비마이너",
    "ablenews": "에이블뉴스",
    "theindigo": "더인디고",
    "sisain": "시사인",
}

PRIMARY_COMPARISON_SOURCES = frozenset(
    {"chosun", "joongang", "donga", "hani", "khan", "ohmynews", "pressian", "sisain"}
)
LABOR_ALTERNATIVE_SOURCES = frozenset({"newscham", "labortoday"})
DESIGNATED_COLUMN_SOURCES = frozenset({"mediaus"})
DISABILITY_PRESS_SOURCES = frozenset({"beminor", "ablenews", "theindigo"})
PRINT_DIGITAL_SOURCES = frozenset(
    PRIMARY_COMPARISON_SOURCES
    | LABOR_ALTERNATIVE_SOURCES
    | DISABILITY_PRESS_SOURCES
    | DESIGNATED_COLUMN_SOURCES
)
OPINION_FULL_SCAN_SOURCES = PRIMARY_COMPARISON_SOURCES

# 장애 브리핑의 당일 신규 선정 기사는 장애 전문 언론 3사(비마이너·에이블뉴스·더인디고)와
# 보수·진보 종합매체 8사(PRIMARY_COMPARISON_SOURCES)로 구성된 11개 지정매체로 한정한다.
# 일반 전문 언론(매일노동뉴스·참세상)은 노동 섹션 전용이라 여기서 제외된다.
DISABILITY_SECTION_ALLOWED_SOURCES = DISABILITY_PRESS_SOURCES | PRIMARY_COMPARISON_SOURCES

# II절(노동·돌봄·빈곤)의 당일 신규 선정 기사는 일반 전문 언론 2사(매일노동뉴스·참세상)와
# 보수·진보 종합매체 8사, 그리고 장애 전문 언론 가운데 비마이너 1사로 구성된 11개
# 지정매체로 한정한다. 에이블뉴스·더인디고는 장애 섹션 전용이라 여기서 제외된다.
# mediaus(DESIGNATED_COLUMN_SOURCES)는 애초에 포함되지 않으므로 동일 주제 이전 보도
# 참고용으로만 쓸 수 있고 당일 신규 이슈의 근거 기사로는 못 쓴다.
LABOR_SECTION_ALLOWED_SOURCES = (
    PRIMARY_COMPARISON_SOURCES | LABOR_ALTERNATIVE_SOURCES | frozenset({"beminor"})
)

# III절(칼럼)에 반드시 포함하는 고정 필자 칼럼. 장애 관련 토픽 분류와 무관하게 매번 확인
# 대상이므로 수집(pipeline)·대기열 선정(editorial)·발행 게이트(assurance) 각 단계에서
# 일반 후보와 다르게 취급한다.
#
# 각 항목은 "모두 일치해야 하는 표지"다. 필자명 하나만으로는 동명이인에 오탐이 난다 —
# 미디어스 김민하(칼럼니스트)는 동명의 배우와 이름이 같아 연예 기사가 실제로 수집되고
# 있으므로, 칼럼 바이라인 형식("[미디어스=김민하 칼럼]")에 항상 있는 "칼럼"을 함께 요구한다.
MANDATORY_OPINION_COLUMNS: dict[str, tuple[str, ...]] = {
    "hani": ("세계의 창", "지제크"),
    "mediaus": ("김민하", "칼럼"),
    "khan": ("고병권", "묵묵"),
}


def is_mandatory_opinion_column(source: str, *texts: str | None) -> bool:
    """Match a designated column across whatever metadata fields the caller has.

    Callers pass the fields themselves rather than a pre-joined string: the
    columnist's name lives in a different field per outlet (경향은 제목,
    한겨레·미디어스는 summary의 바이라인 줄), and an earlier fix silently missed
    한겨레 지제크 precisely because one call site joined title/byline/section but
    left summary out.
    """

    terms = MANDATORY_OPINION_COLUMNS.get(source)
    if not terms:
        return False
    haystack = " ".join(value for value in texts if value)
    return all(term in haystack for term in terms)


# 장애정책 핵심 기관·부처의 인사 소식을 토픽 분류와 무관하게 후보로 강제 편입하기 위한
# 감시 대상. 기관·직위명만으로는 그 기관을 다루는 거의 모든 정책 기사에 걸린다(예:
# "보건복지부 장관은 오늘 ~라고 밝혔다"는 매일 나오는 문장이지 인사 소식이 아니다). 실제
# 인사 이동을 가리키는 이벤트 용어가 함께 나올 때만 매칭해 오탐을 줄인다.
PERSONNEL_EVENT_TERMS: tuple[str, ...] = (
    "임명",
    "내정",
    "취임",
    "선임",
    "지명",
    "신임",
    "발탁",
    "이임",
    "퇴임",
    "사임",
    "사퇴",
    "물러나",
    "후보자",
    "교체",
)


@dataclass(frozen=True)
class PersonnelTarget:
    label: str
    anchor: str
    positions: tuple[str, ...]
    severity: AuditSeverity


# anchor는 다른 기관과 겹치지 않는 고유 표현을 쓴다("장애인정책국"은 보건복지부에만,
# "통합고용정책국"은 고용노동부에만 있으므로 상위 부처명 없이도 특정된다). "장관"·"차관"처럼
# 정부부처라면 어디에나 있는 직위는 부처명 자체를 anchor로 삼는다.
#
# severity는 이 기관장급(원장·이사장·장관·차관·국장급)이 대기열에 있는데 초안의 선정에도
# 제외에도 없으면 발행을 막는 fatal, 하위 조직장·과장급은 감사 보고에만 남기는 warning으로
# 나눈다. 과장급 전보 같은 빈번한 인사로 발행 전체가 막히는 것을 피하기 위함이다.
MONITORED_PERSONNEL_TARGETS: tuple[PersonnelTarget, ...] = (
    PersonnelTarget("한국장애인개발원 원장", "한국장애인개발원", ("원장",), AuditSeverity.FATAL),
    PersonnelTarget(
        "한국장애인개발원 본부장", "한국장애인개발원", ("본부장",), AuditSeverity.WARNING
    ),
    PersonnelTarget(
        "중앙장애인지역사회통합지원센터 센터장",
        "중앙장애인지역사회통합지원센터",
        ("센터장",),
        AuditSeverity.WARNING,
    ),
    PersonnelTarget(
        "한국장애인고용공단 이사장", "한국장애인고용공단", ("이사장",), AuditSeverity.FATAL
    ),
    PersonnelTarget(
        "한국장애인고용공단 실국장", "한국장애인고용공단", ("실국장",), AuditSeverity.WARNING
    ),
    PersonnelTarget("국민연금공단 이사장", "국민연금공단", ("이사장",), AuditSeverity.FATAL),
    PersonnelTarget("보건복지부 장관", "보건복지부", ("장관",), AuditSeverity.FATAL),
    PersonnelTarget("보건복지부 차관", "보건복지부", ("차관",), AuditSeverity.FATAL),
    PersonnelTarget("보건복지부 장애인정책국 국장", "장애인정책국", ("국장",), AuditSeverity.FATAL),
    PersonnelTarget(
        "보건복지부 장애인정책과 과장", "장애인정책과", ("과장",), AuditSeverity.WARNING
    ),
    PersonnelTarget("고용노동부 장관", "고용노동부", ("장관",), AuditSeverity.FATAL),
    PersonnelTarget("고용노동부 차관", "고용노동부", ("차관",), AuditSeverity.FATAL),
    PersonnelTarget(
        "고용노동부 통합고용정책국 국장", "통합고용정책국", ("국장",), AuditSeverity.FATAL
    ),
    PersonnelTarget(
        "고용노동부 장애인고용과 과장", "장애인고용과", ("과장",), AuditSeverity.WARNING
    ),
)


def monitored_personnel_match(*texts: str | None) -> PersonnelTarget | None:
    """Return the first monitored-institution personnel target this text matches.

    Callers pass the fields themselves (title/byline/section/summary), mirroring
    is_mandatory_opinion_column. Requires an explicit personnel-event term in
    addition to anchor+position so routine policy coverage of these institutions
    doesn't match.
    """

    haystack = " ".join(value for value in texts if value)
    if not haystack or not any(term in haystack for term in PERSONNEL_EVENT_TERMS):
        return None
    for target in MONITORED_PERSONNEL_TARGETS:
        if target.anchor in haystack and any(position in haystack for position in target.positions):
            return target
    return None


# 논조 비교에서 실제 기사 텍스트를 읽은 뒤 결과를 묶어 설명하는 용도로만 쓴다.
# 매체 내용을 읽지 않고 이 라벨만으로 논조를 추정하는 데 쓰지 않는다.
SOURCE_CAMP = {
    "chosun": "보수",
    "joongang": "보수",
    "donga": "보수",
    "newscham": "진보",
    "pressian": "진보",
    "ohmynews": "진보",
    "khan": "진보",
    "hani": "진보",
    "sisain": "진보",
    "beminor": "전문지",
    "ablenews": "전문지",
    "theindigo": "전문지",
    "labortoday": "전문지",
}
