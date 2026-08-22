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
