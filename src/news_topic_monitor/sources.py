from __future__ import annotations

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
}

PRIMARY_COMPARISON_SOURCES = frozenset(
    {"chosun", "joongang", "donga", "hani", "khan", "ohmynews", "pressian"}
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

# II절(노동·돌봄·빈곤)의 당일 신규 선정 기사는 census/역검증 의무를 지는 12개
# 지정매체로만 한정한다. mediaus(DESIGNATED_COLUMN_SOURCES)는 여기서 제외되므로
# 동일 주제 이전 보도 참고용으로만 쓸 수 있고 당일 신규 이슈의 근거 기사로는 못 쓴다.
LABOR_SECTION_ALLOWED_SOURCES = (
    PRIMARY_COMPARISON_SOURCES | LABOR_ALTERNATIVE_SOURCES | DISABILITY_PRESS_SOURCES
)

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
    "beminor": "전문지",
    "ablenews": "전문지",
    "theindigo": "전문지",
    "labortoday": "전문지",
}
