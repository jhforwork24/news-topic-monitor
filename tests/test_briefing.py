from __future__ import annotations

from datetime import UTC, datetime, timedelta

from news_topic_monitor.briefing import (
    PREVIOUS_COVERAGE_MAX_AGE_DAYS,
    PREVIOUS_COVERAGE_MAX_ITEMS,
    PREVIOUS_COVERAGE_NOTE_LIMIT,
    PREVIOUS_MATCH_ANCHORED,
    PREVIOUS_MATCH_PROPER_NOUN,
    BriefingDocument,
    BriefingIssue,
    BriefingSection,
    PreviousCoverage,
    analyze_tone,
    build_briefing,
    is_opinion,
    issue_analysis_text,
    labor_previous_history,
    previous_coverage_for,
    previous_coverage_notes,
    previous_coverage_tokens,
    render_briefing_markdown,
    summarize_issue,
)
from news_topic_monitor.briefing_validation import validate_briefing
from news_topic_monitor.models import (
    ArticleRecord,
    BodyStatus,
    Classification,
    VerificationStatus,
)
from news_topic_monitor.storage import JsonlStorage


def _article(
    source: str,
    title: str,
    *,
    classification: Classification = Classification.RELEVANT,
    section: str = "사회",
    article_id: str = "1",
) -> ArticleRecord:
    now = datetime(2026, 8, 15, 1, tzinfo=UTC)
    return ArticleRecord(
        source=source,
        article_id=f"{source}-{article_id}",
        canonical_url=f"https://example.com/{source}/{article_id}",
        title=title,
        section=section,
        published_at=now,
        updated_at=None,
        first_seen_at=now,
        last_seen_at=now,
        summary="공개 요약",
        monitor_summary="자동 모니터 요약",
        body_status=BodyStatus.FETCHED,
        content_hash="hash",
        classification=classification,
        topic_score=10.0,
        matched_terms=["장애인", "이동권"] if classification != Classification.IRRELEVANT else [],
        excluded_terms=[],
        classification_reason="규칙 판정",
        verification_status=VerificationStatus.BODY_VERIFIED,
        collection_error=None,
    )


def test_three_section_briefing_and_opinion_column(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    rows = [
        _article("hani", "장애인 이동권 보장 촉구", article_id="1"),
        _article(
            "labortoday",
            "돌봄노동자 임금 교섭",
            classification=Classification.IRRELEVANT,
            article_id="2",
        ),
        _article("khan", "[칼럼] 장애인 이동권을 시민권으로", section="오피니언", article_id="4"),
    ]
    for row in rows:
        storage.upsert(row)
    storage.write_health(
        {
            "sources": {
                source: {"success": True, "errors": []} for source in {row.source for row in rows}
            }
        }
    )
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    assert [section.title for section in document.sections] == [
        "I. 장애정책·장애인운동",
        "II. 노동·돌봄·빈곤·소수자",
        "III. 주요 칼럼",
    ]
    assert document.sections[1].issues
    assert document.sections[2].issues
    text = render_briefing_markdown(document, crpd_url="https://notion.example/crpd")
    assert text.index("I. 장애정책") < text.index("II. 노동") < text.index("III. 주요 칼럼")
    assert "### 이슈 요약·보도 논조" in text
    assert "### 기사 요약" not in text
    assert "### 보도 논조" not in text
    assert "| 언론사 | 기사 | 발행 |" not in text
    assert "KST" not in text
    assert "오늘의 변화" not in text
    assert "# 점검" not in text
    validate_briefing(document)


def test_issue_analysis_text_separates_summary_and_tone_with_blank_line() -> None:
    issue = BriefingIssue(
        title="장애인 이동권 보장 촉구",
        articles=[_article("hani", "장애인 이동권 보장 촉구")],
        summary="전장연이 이동권 보장을 촉구하는 기자회견을 열었다.",
        tone_analysis="한겨레는 요구사항을 중심으로 전했다.",
    )
    text = issue_analysis_text(issue)
    assert text == (
        "전장연이 이동권 보장을 촉구하는 기자회견을 열었다.\n\n한겨레는 요구사항을 중심으로 전했다."
    )


def test_opinion_detection() -> None:
    assert is_opinion(_article("donga", "[사설] 이동권은 시민권이다", section="오피니언"))
    assert not is_opinion(_article("donga", "이동권 집회 현장 보도"))
    article = _article("beminor", "평택 정신의료기관 입원환자 40명 집단 전원")
    article.summary = "사설구급차로 환자를 옮겼다는 보도다."
    assert not is_opinion(article)


def test_opinion_detection_recognizes_named_column_bracket() -> None:
    # Regression test: a real editorial-finalize run rejected the Sisain
    # column "장애인 시설 이름에 '식민지'가 붙은 이유 [김승섭의 공부]" because
    # none of OPINION_TERMS appeared in its title — "공부" was not a
    # recognized marker, unlike the "묵묵"/"세계의 창" columns Claude had
    # already seen once. The trailing "[필자명의 시리즈명]" bracket is the
    # actual convention Korean outlets use for a named personal column.
    assert is_opinion(
        _article(
            "sisain",
            "장애인 시설 이름에 '식민지'가 붙은 이유 [김승섭의 공부]",
        )
    )
    # A plain reported-news bracket like "[단독]" must not be mistaken for a
    # named column just because it contains brackets.
    assert not is_opinion(_article("sisain", "[단독] 장애인 시설 인권침해 실태조사"))


def test_previous_coverage_requires_a_specific_shared_concept() -> None:
    color = _article(
        "beminor",
        "색동원 거주인 33명 전원 자립 약속",
        section="탈시설·자립생활",
        article_id="color",
    )
    unrelated = _article(
        "donga",
        "국힘, 현역 포함 당협위원장 전원 재선출 추진",
        classification=Classification.IRRELEVANT,
        article_id="politics",
    )
    color_previous = previous_coverage_for([color], [unrelated])
    assert not any(item.url == unrelated.canonical_url for item in color_previous)

    current_access = _article(
        "ablenews",
        "제주 섭지코지 산책로 이동권 개선 인권위 권고 수용",
        article_id="access-current",
    )
    earlier_access = _article(
        "theindigo",
        "해안 산책로 안전한 이동 보장, 지자체 인권위 권고 수용",
        article_id="access-earlier",
    )
    access_previous = previous_coverage_for([current_access], [earlier_access])
    assert any(item.url == earlier_access.canonical_url for item in access_previous)


def test_previous_coverage_attaches_a_shared_system_name_even_without_other_shared_words() -> None:
    current = _article(
        "donga",
        "첼로 배우고 싶어요 16세 소녀 꿈 이뤄준 개인예산제",
        article_id="system-current",
    )
    critical = _article(
        "ablenews",
        "선택권 보장 개인예산제 영양제·가전제품 구매 부적절",
        article_id="system-critical",
    )
    unrelated = _article(
        "beminor",
        "장애인 활동지원 시간 확대 요구 기자회견",
        article_id="system-unrelated",
    )
    previous = previous_coverage_for([current], [critical, unrelated])
    assert [item.url for item in previous] == [critical.canonical_url]
    assert previous[0].match_rule == PREVIOUS_MATCH_PROPER_NOUN

    bill_current = _article("hani", "메가특구법 주52시간 예외 장관 발언", article_id="bill-current")
    bill_earlier = _article(
        "joongang",
        "메가특구법 첫 사회적 대화 노동계 첫마디는 노동권 후퇴",
        article_id="bill-earlier",
    )
    bill_previous = previous_coverage_for([bill_current], [bill_earlier])
    assert [item.url for item in bill_previous] == [bill_earlier.canonical_url]


def test_previous_coverage_links_differently_worded_reports_via_concept_terms() -> None:
    current = _article(
        "ablenews",
        "특별교통수단은 장애인의 필수적 이동지원, 예산 부족 이동권 축소 안 된다",
        article_id="current",
    )
    current.summary = "경기도 특별교통수단 운영예산이 부족해 장애인콜택시 이용에 차질이 우려된다."
    earlier = _article(
        "beminor",
        "추미애 필수 예산 끝까지 지킨다더니, 장애인콜택시 예산은 삭감",
        article_id="earlier",
    )
    earlier.summary = "경기도가 특별교통수단(장애인콜택시) 예산을 삭감해 장애인 이동권이 후퇴했다."
    previous = previous_coverage_for([current], [earlier])
    assert any(item.url == earlier.canonical_url for item in previous)


def test_previous_coverage_ignores_generic_court_procedure_overlap() -> None:
    ablenews = _article(
        "ablenews",
        "색동원 성폭력 피해 고스란히 인정받지 못했다 항거불능 벽에 막힌 1심",
        section="인권",
        article_id="ablenews",
    )
    beminor = _article(
        "beminor",
        "색동원 시설장 징역 15년 선고했지만 위력 외면한 법원, 강간 혐의 일부 무죄",
        section="탈시설·자립생활",
        article_id="beminor",
    )
    theindigo = _article(
        "theindigo",
        "법원, 색동원 시설장 징역 15년 장애계 강간 혐의 무죄 비판",
        article_id="theindigo",
    )
    unrelated_verdict = _article(
        "khan",
        "1타 강사 현우진, 문항 거래 혐의 1심 무죄 법원 주고받은 금품 사적 거래 해당",
        classification=Classification.IRRELEVANT,
        article_id="hyunwoojin",
    )
    previous = previous_coverage_for([ablenews, beminor, theindigo], [unrelated_verdict])
    assert not any(item.url == unrelated_verdict.canonical_url for item in previous)


def test_previous_coverage_ignores_incidental_rights_category_overlap() -> None:
    budget_ablenews = _article(
        "ablenews",
        "전장연 요구 내년 장애인권리예산, 이동권 포함됐지만 교육 노동 탈시설 외면",
        article_id="ablenews-budget",
    )
    budget_beminor = _article(
        "beminor",
        "전장연, 내년 예산 장애인 권리보장 아닌 차별예산 국회 투쟁 예고",
        article_id="beminor-budget",
    )
    budget_beminor.summary = (
        "지역사회에서 함께 살기 위한 이동권, 노동권, 교육권, 자립생활, 탈시설 예산을 요구했다."
    )
    unrelated_rare_disease = _article(
        "ohmynews",
        "서다운 희귀질환 아동 어디서나 동등한 교육 건강권 보장해야",
        article_id="rare-disease",
    )
    unrelated_rare_disease.summary = (
        "서다운 의원이 희귀질환 아동의 교육권과 건강권 보장 지원체계 마련을 촉구했다."
    )
    previous = previous_coverage_for([budget_ablenews, budget_beminor], [unrelated_rare_disease])
    assert not any(item.url == unrelated_rare_disease.canonical_url for item in previous)


def test_previous_coverage_ignores_incidental_program_name_overlap() -> None:
    budget_indigo = _article(
        "theindigo",
        "정부, 2027년 통합돌봄 예산 1558억원 편성, 시민사회 요구 6447억원에 크게 못 미쳐",
        article_id="indigo-budget",
    )
    budget_indigo.summary = (
        "돌봄재정 확대 공동행동이 2027년도 통합돌봄 예산안 1558억원을 비판하며 증액을 요구했다."
    )
    budget_ablenews = _article(
        "ablenews",
        "통합돌봄 정부안 1558억원 턱없이 부족",
        article_id="ablenews-budget",
    )
    budget_ablenews.summary = "돌봄취약지역 인프라 예산이 요구액에 크게 못 미친다는 지적이 나왔다."
    unrelated_service_linkage = _article(
        "ablenews",
        "실로암시각장복, 고령 시각장애인 대상 맞춤형 주거의료 돌봄 서비스 연계",
        article_id="silloam",
    )
    unrelated_service_linkage.summary = (
        "한국형 통합돌봄모형 구축 사업의 일환으로 고령 시각장애인 11명에게 돌봄 서비스를 연계했다."
    )
    previous = previous_coverage_for([budget_indigo, budget_ablenews], [unrelated_service_linkage])
    assert not any(item.url == unrelated_service_linkage.canonical_url for item in previous)


def test_previous_coverage_ignores_incidental_company_name_overlap() -> None:
    wage_deal = _article(
        "donga",
        "현대차 노사, 올해 임협 타결 성과급 400%+1270만원 지급",
        article_id="hyundai-wage",
    )
    wage_deal.summary = "현대차 노사가 10년만의 전면파업 진통 끝에 올해 임금협상을 최종 타결했다."
    unrelated_oman_mou = _article(
        "chosun",
        "현대차그룹, 오만 정부와 친환경 모빌리티 협력 MOU 체결",
        article_id="hyundai-oman",
    )
    unrelated_oman_mou.summary = (
        "현대차그룹이 오만 정부와 수소전기버스·초고속 전기충전기 투입에 합의했다."
    )
    previous = previous_coverage_for([wage_deal], [unrelated_oman_mou])
    assert not any(item.url == unrelated_oman_mou.canonical_url for item in previous)


def test_previous_coverage_ignores_incidental_legal_category_overlap() -> None:
    hlmando_death = _article(
        "labortoday",
        "HL만도 사망사고 유족 노동부에 조사 내용 공개하라",
        article_id="hlmando-death",
    )
    hlmando_death.summary = (
        "HL만도 평택공장 하청노동자 사망사고 유족이 중대재해 조사 결과 공개를 요구했다."
    )
    unrelated_shipyard_death = _article(
        "hani",
        "현대중공업 올해만 5명 중대재해 사망 노조 노동장관 면담 요구",
        article_id="hhi-death",
    )
    unrelated_shipyard_death.summary = (
        "현대중공업에서 올해만 5명이 중대재해로 사망해 노조가 대책을 촉구했다."
    )
    previous = previous_coverage_for([hlmando_death], [unrelated_shipyard_death])
    assert not any(item.url == unrelated_shipyard_death.canonical_url for item in previous)


def test_previous_coverage_ignores_incidental_retailer_name_overlap() -> None:
    rehab_plan = _article(
        "khan",
        "법원, 홈플러스 회생계획안 인가",
        article_id="homeplus-rehab",
    )
    rehab_plan.summary = "법원이 홈플러스 회생계획안을 인가해 채권자 75.9%가 찬성했다고 밝혔다."
    unrelated_award = _article(
        "labortoday",
        "홈플러스 1546억 체불 청산 노동부 우수직원 포상",
        article_id="homeplus-award",
    )
    unrelated_award.summary = "노동부가 홈플러스 체불임금 1546억원 청산에 기여한 직원을 포상했다."
    previous = previous_coverage_for([rehab_plan], [unrelated_award])
    assert not any(item.url == unrelated_award.canonical_url for item in previous)


def test_build_briefing_excludes_irrelevant_history_from_previous_coverage(
    tmp_path, topics_path
) -> None:
    storage = JsonlStorage(tmp_path)
    current = _article(
        "ablenews",
        "제주 섭지코지 산책로 이동권 개선 인권위 권고 수용",
        article_id="current",
    )
    current.byline = "홍길동 기자"
    storage.upsert(current)
    off_topic_history = _article(
        "donga",
        "제주 섭지코지 산책로 이동권 개선 관련 후속 대책 발표",
        classification=Classification.IRRELEVANT,
        article_id="off-topic-previous",
    )
    off_topic_history.published_at = datetime(2026, 8, 14, 0, tzinfo=UTC)
    off_topic_history.first_seen_at = off_topic_history.published_at
    off_topic_history.last_seen_at = off_topic_history.published_at
    storage.upsert(off_topic_history)
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    issue = document.sections[0].issues[0]
    assert not any(item.url == off_topic_history.canonical_url for item in issue.previous_coverage)


def test_previous_coverage_prefers_more_detailed_report_at_equal_relevance() -> None:
    current = _article(
        "hani",
        "권리중심공공일자리 예산 국회 심의",
        section="탈시설·자립생활",
        article_id="budget-current",
    )
    thin_report = _article(
        "donga",
        "권리중심공공일자리 관련 단신",
        article_id="thin",
    )
    thin_report.verification_status = VerificationStatus.METADATA_ONLY
    thin_report.summary = "짧은 단신."
    detailed_report = _article(
        "hani",
        "권리중심공공일자리 예산 편성 경과 보도",
        article_id="detailed",
    )
    detailed_report.verification_status = VerificationStatus.BODY_VERIFIED
    detailed_report.summary = "예산 편성 경과와 정부·지자체 입장을 상세히 다룬 보도." * 3

    previous = previous_coverage_for([current], [thin_report, detailed_report])
    assert previous
    assert previous[0].url == detailed_report.canonical_url


def test_previous_coverage_excludes_routine_local_center_training_notices() -> None:
    current = _article(
        "donga",
        "'피터팬 아빠' 발달장애인 지원 호소 통했다… 최중증 24시간 일대일 돌봄 주말까지 확대",
        article_id="care-plan-current",
    )
    seoul_center = _article(
        "ablenews",
        "개발원 서울센터, 최중증 발달장애인 통합돌봄 종사자 역량 강화·교류 프로그램 운영",
        article_id="seoul-center",
    )
    daejeon_center = _article(
        "ablenews",
        "개발원 대전센터, '최중증 발달장애인 통합돌봄서비스 사례자문회의' 성료",
        article_id="daejeon-center",
    )
    jeonbuk_center = _article(
        "ablenews",
        "전북 최중증 발달장애인 통합돌봄 종사자 대상 도전행동 대응 교육 실시",
        article_id="jeonbuk-center",
    )
    previous = previous_coverage_for([current], [seoul_center, daejeon_center, jeonbuk_center])
    assert previous == []

    national_policy = _article(
        "ablenews",
        "한국장애인개발원, 최중증 발달장애인 통합돌봄 실태조사 결과 발표",
        article_id="national-policy",
    )
    previous_with_policy = previous_coverage_for([current], [national_policy])
    assert any(item.url == national_policy.canonical_url for item in previous_with_policy)


def _dated(article: ArticleRecord, day: int, hour: int = 1) -> ArticleRecord:
    article.published_at = datetime(2026, 9, day, hour, tzinfo=UTC)
    article.first_seen_at = article.published_at
    article.last_seen_at = article.published_at
    return article


def test_previous_coverage_needs_a_rare_shared_word_when_concepts_are_common() -> None:
    """Regression: '최중증'+'발달장애' alone tied unrelated issues to '피터팬 아빠' reports.

    In a realistic pool those words appear in many titles, so they no longer pin a
    topic down. A word rare in the pool (a hospital name) still does.
    """

    filler = [
        _article(
            "ablenews",
            f"최중증 발달장애인 돌봄 지원 사업 안내 {index}",
            article_id=f"filler-{index}",
        )
        for index in range(30)
    ]
    for item in filler:
        item.summary = "최중증 발달장애인 돌봄 서비스를 안내했다."
    petition = _article(
        "ablenews",
        "주간이용시설 국고보조 전환 국민동의청원",
        article_id="petition",
    )
    petition.summary = "최중증 발달장애인이 다니는 주간이용시설의 국고보조 전환을 요구했다."
    peter_pan = _article(
        "joongang",
        "'피터팬 아빠'가 남긴 숙제, 가족이 감당하던 발달장애인 돌봄 지원 강화",
        article_id="peter-pan",
    )
    peter_pan.summary = "최중증 발달장애인 돌봄에 대한 정부 지원이 강화된다."
    assert previous_coverage_for([petition], [*filler, peter_pan]) == []

    transfer = _article(
        "hani",
        "반구대병원 전원 환자 또 재전원",
        article_id="transfer",
    )
    transfer.summary = "폐업한 정신의료기관 반구대병원에서 옮겨간 환자들이 다시 전원됐다."
    closure = _article("hani", "반구대병원 폐원 앞두고 환자 전원 계획", article_id="closure")
    closure.summary = "울산 정신의료기관 반구대병원이 폐원을 앞두고 환자 전원 계획을 냈다."
    matched = previous_coverage_for([transfer], [*filler, closure])
    assert [item.url for item in matched] == [closure.canonical_url]


def test_previous_coverage_shows_a_dated_timeline_of_turning_points() -> None:
    # 실제 이력 풀 규모(수백~수천 건)에서 사안명은 드물다. 작은 풀에서는 모든 기사에
    # 들어간 사안명이 흔한 말로 계산되므로 무관한 기사로 풀 크기를 맞춘다.
    unrelated = [
        _article("ablenews", f"지역 소식 {index}", article_id=f"local-{index}")
        for index in range(800)
    ]
    current = _dated(_article("hani", "반구대병원 전원 환자 또 재전원", article_id="current"), 30)
    current.summary = "정신의료기관 반구대병원에서 옮겨간 환자들이 다시 전원됐다."
    history = [
        _dated(_article("hani", "반구대병원 폐원 결정 환자 전원 계획", article_id="a"), 1),
        _dated(_article("hani", "반구대병원 환자 82% 다시 정신병원으로", article_id="b"), 7),
        _dated(_article("donga", "반구대병원 환자 다시 정신병원 전원 논란", article_id="c"), 7, 5),
        _dated(_article("hani", "반구대병원 발달장애인 창틀 강박 병원 전원", article_id="d"), 12),
        _dated(_article("khan", "반구대병원 학대 정황 확인", article_id="e"), 15),
        _dated(_article("hani", "반구대병원 테라스 폭행 사건 드러나", article_id="f"), 20),
        _dated(_article("ablenews", "반구대병원 관련 소식", article_id="g"), 22),
        _dated(_article("beminor", "반구대병원 전원 대책 촉구", article_id="h"), 25),
    ]
    for item in history:
        item.summary = "울산의 정신의료기관 반구대병원 관련 보도다."
    matched = previous_coverage_for([current], [*unrelated, *history])

    assert len(matched) == PREVIOUS_COVERAGE_MAX_ITEMS
    published = [item.published for item in matched]
    assert published == sorted(published)
    assert len(set(published)) == len(published)
    # 폐원 결정은 첫 보도이자 전환점이라 반드시 남는다.
    assert history[0].canonical_url in {item.url for item in matched}


def test_previous_coverage_matches_a_name_with_a_particle_attached() -> None:
    current = _article("beminor", "정신병원서 잇단 사망, 국가 책임 촉구", article_id="current")
    current.summary = "정신의료기관 입원 환자의 잇단 사망에 단체들이 국가 책임을 촉구했다."
    earlier = _article("joongang", "반년간 4명 숨진 정신병원 충격", article_id="earlier")
    earlier.summary = "인천의 정신의료기관에서 환자 4명이 숨졌다."
    matched = previous_coverage_for([current], [earlier])
    assert [item.url for item in matched] == [earlier.canonical_url]


def test_previous_coverage_accepts_three_shared_words_when_one_pins_the_topic() -> None:
    current = _article("labortoday", "HL만도 중대재해 안전대책 발표에 유족 반발", article_id="cur")
    current.summary = "평택공장 사망사고 유족이 회사 대책을 비판했다."
    earlier = _article("pressian", "HL만도 끼임사 청년 유족, 회장 책임 촉구", article_id="prev")
    earlier.summary = "평택공장에서 숨진 하청노동자의 유족이 책임을 물었다."
    matched = previous_coverage_for([current], [earlier])
    assert [item.url for item in matched] == [earlier.canonical_url]
    assert matched[0].match_rule == PREVIOUS_MATCH_ANCHORED


def test_labor_previous_history_uses_the_labor_classifier(topics_path) -> None:
    start = datetime(2026, 10, 9, 0, tzinfo=UTC)
    sit_in = _dated(
        _article(
            "labortoday",
            "한화오션 하청노동자 고공농성 돌입, 원청교섭 요구",
            classification=Classification.IRRELEVANT,
            article_id="sit-in",
        ),
        30,
    )
    sit_in.summary = "하청노동자들이 원청 사용자와의 교섭을 요구했다."
    baseball = _dated(
        _article(
            "hani",
            "프로야구 개막전 매진",
            classification=Classification.IRRELEVANT,
            article_id="baseball",
        ),
        30,
    )
    baseball.summary = "개막전 입장권이 모두 팔렸다."
    not_designated = _dated(
        _article(
            "ablenews",
            "한화오션 하청노동자 고공농성 돌입, 원청교섭 요구",
            classification=Classification.IRRELEVANT,
            article_id="not-designated",
        ),
        30,
    )
    not_designated.summary = sit_in.summary

    history = labor_previous_history(
        [sit_in, baseball, not_designated], start=start, topics_path=topics_path
    )
    assert history == [sit_in]

    talks = _article(
        "labortoday",
        "한화오션 하청노동자 고공농성 해제, 원청교섭 시작",
        classification=Classification.IRRELEVANT,
        article_id="talks",
    )
    matched = previous_coverage_for([talks], history)
    assert [item.url for item in matched] == [sit_in.canonical_url]


def test_labor_previous_history_keeps_working_hours_reports(topics_path) -> None:
    """Regression: 2026-10-27 초광역특구법 — 노동시간 특례 보도가 분류기에서 빠졌다."""

    start = datetime(2026, 10, 9, 0, tzinfo=UTC)
    hours = _dated(
        _article(
            "hani",
            "반도체 연구직 주52시간 예외 두고 정부 이견",
            classification=Classification.IRRELEVANT,
            article_id="hours",
        ),
        30,
    )
    hours.summary = "정부 부처가 연구개발 인력의 노동시간 특례 기준을 놓고 맞섰다."
    baseball = _dated(
        _article(
            "hani",
            "프로야구 개막전 매진",
            classification=Classification.IRRELEVANT,
            article_id="baseball",
        ),
        30,
    )
    baseball.summary = "개막전 입장권이 모두 팔렸다."

    history = labor_previous_history([hours, baseball], start=start, topics_path=topics_path)
    assert history == [hours]


def test_previous_coverage_reads_law_names_from_the_summary_and_folds_renames() -> None:
    """Regression: 2026-10-27 — 법명이 요약에만 있고, 메가특구법이 초광역특구법으로 개명됐다."""

    earlier = _dated(
        _article("hani", "당정, 이달 발의 방침…연구 개발자 예외 검토", article_id="mega"),
        4,
    )
    earlier.summary = "당정이 메가특구법을 이달 중 발의하기로 했다."
    unrelated = _dated(
        _article("hani", "김정재 의원, 주차구역 이용 개정안 발의", article_id="parking"),
        5,
    )
    unrelated.summary = "현행법은 장애인 전용 주차구역 이용 대상을 정하고 있다."
    current = _article("donga", "李 시행령 수정 검토 주문", article_id="today")
    current.published_at = datetime(2026, 10, 24, 1, tzinfo=UTC)
    current.summary = "대통령이 초광역특구법안의 노동 특례 대신 현행법 시행령 수정을 주문했다."

    matched = previous_coverage_for([current], [earlier, unrelated])
    assert [item.url for item in matched] == [earlier.canonical_url]
    assert matched[0].match_rule == PREVIOUS_MATCH_PROPER_NOUN
    assert "초광역특구법" in matched[0].matched_tokens


def test_previous_coverage_folds_52_hour_spellings() -> None:
    first = _article("khan", "주52시간제 예외 논란", article_id="a")
    second = _article("donga", "반도체 주52시간 예외 재검토", article_id="b")
    assert "주52시간" in previous_coverage_tokens(first) & previous_coverage_tokens(second)


def test_korean_particles_for_single_and_multi_article_tone() -> None:
    column = _article("khan", "장애인 이동권 보도")
    assert analyze_tone([column]) == ""

    movement = _article("khan", "장애인 이동권 보장 촉구")
    assert analyze_tone([movement]).startswith("경향신문은 ")


def test_column_section_is_completely_omitted_when_no_column_exists(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    storage.upsert(_article("hani", "장애인 이동권 보장 촉구"))
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    assert [section.title for section in document.sections] == [
        "I. 장애정책·장애인운동",
        "II. 노동·돌봄·빈곤·소수자",
    ]
    assert "주요 칼럼" not in document.overview
    assert "칼럼 0" not in document.overview
    text = render_briefing_markdown(document, crpd_url=None)
    assert "III. 주요 칼럼" not in text
    assert "주요 칼럼" not in text


def test_markdown_article_link_escapes_brackets(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    storage.upsert(_article("hani", "[현장] 장애인 이동권 | 보도"))
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    text = render_briefing_markdown(document, crpd_url=None)
    assert r"[\[현장\] 장애인 이동권 \| 보도](https://example.com/hani/1)" in text


def test_editorial_exclusions_section_assignment_and_previous_coverage(
    tmp_path, topics_path
) -> None:
    storage = JsonlStorage(tmp_path)
    rows = [
        _article(
            "beminor",
            "색동원 거주인 전원 자립생활 약속",
            section="탈시설·자립생활",
            article_id="saekdongwon",
        ),
        _article(
            "ablenews",
            "경북도의회 위원장, 장애인공단 경북지사 방문 의견 청취",
            article_id="visit",
        ),
        _article(
            "ablenews",
            "한뇌협, 뇌병변장애인 평생교육 장학생 모집",
            article_id="scholarship",
        ),
        _article(
            "ablenews",
            "UN CRPD 채택 20주년 국제장애인권컨퍼런스 개최",
            article_id="crpd",
        ),
        _article(
            "ablenews",
            "최중증 발달장애인 통합돌봄 제공기관 현장 소통",
            article_id="care",
        ),
        _article(
            "ablenews",
            "서울시의회 장애인 이동권·권리중심공공일자리 조례 발의",
            article_id="ordinance",
        ),
        _article(
            "hani",
            "하청노동자 보양식 세트 제외와 일부 임금체불",
            classification=Classification.IRRELEVANT,
            article_id="gift",
        ),
        _article(
            "ablenews",
            "시각장애인용 '폭우·폭염 대비 재난안전가이드' 배포",
            article_id="guide",
        ),
        _article(
            "ablenews",
            "장애인재활협회 한·일 국제 간담회 개최",
            article_id="roundtable",
        ),
        _article(
            "ablenews",
            "EBS 장애인 화면해설방송 이용 안내",
            article_id="guide-broadcast",
        ),
        _article(
            "ablenews",
            "기아 초록여행, 추석 맞이 '장애인 가정 귀성길' 지원",
            article_id="holiday",
        ),
        _article(
            "ablenews",
            "방미통위, '장애인복지·공익채널' 선정 접수 시작",
            article_id="channel",
        ),
    ]
    for row in rows:
        storage.upsert(row)
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    disability_titles = [issue.title for issue in document.sections[0].issues]
    labor_titles = [issue.title for issue in document.sections[1].issues]
    assert not any("경북도의회" in title for title in disability_titles)
    assert not any("장학생 모집" in title for title in disability_titles)
    assert any("최중증 발달장애인 통합돌봄" in title for title in disability_titles)
    assert not any("최중증 발달장애인 통합돌봄" in title for title in labor_titles)
    assert not any("보양식 세트" in title for title in labor_titles)
    assert not any("재난안전가이드" in title for title in disability_titles)
    assert not any("간담회" in title for title in disability_titles)
    assert not any("이용 안내" in title for title in disability_titles)
    assert not any("귀성길" in title for title in disability_titles)
    assert not any("선정 접수" in title for title in disability_titles)
    assert "CRPD 채택 20주년" in disability_titles[-1]
    ordinance = next(issue for issue in document.sections[0].issues if "서울시의회" in issue.title)
    assert any("30268" in (item.url or "") for item in ordinance.previous_coverage)


def test_column_scope_and_mandatory_authors(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    rows = [
        _article(
            "hani",
            "[세계의 창] 지제크의 세계정세 비평",
            classification=Classification.IRRELEVANT,
            section="세계의 창",
            article_id="zizek",
        ),
        _article(
            "mediaus",
            "[김민하 칼럼] 정치의 조건",
            classification=Classification.IRRELEVANT,
            section="김민하 칼럼",
            article_id="minha",
        ),
        _article(
            "khan",
            "[고병권의 묵묵] 함께 사는 법",
            classification=Classification.IRRELEVANT,
            section="묵묵",
            article_id="goby",
        ),
        _article(
            "chosun",
            "[칼럼] 장애인 이동권의 과제",
            classification=Classification.RELEVANT,
            section="오피니언",
            article_id="chosun",
        ),
        _article(
            "ablenews",
            "[칼럼] 장애인 이동권의 과제",
            classification=Classification.RELEVANT,
            section="오피니언",
            article_id="able",
        ),
    ]
    for row in rows:
        storage.upsert(row)
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    columns = next(section for section in document.sections if section.title == "III. 주요 칼럼")
    sources = {article.source for issue in columns.issues for article in issue.articles}
    assert {"hani", "mediaus", "khan", "chosun"} <= sources
    assert "ablenews" not in sources


def test_review_photo_and_entertainment_articles_are_not_auto_published(
    tmp_path, topics_path
) -> None:
    storage = JsonlStorage(tmp_path)
    weather = _article(
        "donga",
        "거제 사흘간 782.5㎜ 물폭탄…주택 침수·주민 100여명 대피",
        classification=Classification.REVIEW,
        article_id="weather",
    )
    photo = _article(
        "khan",
        "[포토뉴스] 이주노동자들 우리에게도 권리가 있습니다",
        classification=Classification.IRRELEVANT,
        article_id="photo",
    )
    entertainment = _article(
        "chosun",
        "왜 정은채였는지 알겠다…재벌X형사2 대체불가 주혜라",
        classification=Classification.IRRELEVANT,
        section="연예",
        article_id="entertainment",
    )
    entertainment.canonical_url = (
        "https://www.chosun.com/entertainments/broadcast/2026/08/16/example"
    )
    music = _article(
        "chosun",
        "남규리 산재 되나요…파워풀 안무에 큰일났네 폭소",
        classification=Classification.IRRELEVANT,
        section="음악",
        article_id="music",
    )
    music.canonical_url = "https://www.chosun.com/entertainments/music/2026/08/16/example"
    labor = _article(
        "labortoday",
        "공공돌봄 노동자 임금과 고용 보장 촉구",
        classification=Classification.IRRELEVANT,
        article_id="labor",
    )
    for row in (weather, photo, entertainment, music, labor):
        storage.upsert(row)

    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    titles = [issue.title for section in document.sections for issue in section.issues]
    assert not any("782.5" in title for title in titles)
    assert not any("포토뉴스" in title for title in titles)
    assert not any("정은채" in title for title in titles)
    assert not any("남규리" in title for title in titles)
    assert any("공공돌봄 노동자" in title for title in titles)
    validate_briefing(document)


def test_issue_summary_and_tone_sentence_limits() -> None:
    single = _article("hani", "장애인 이동권 보장 촉구")
    single.summary = '단체는 "이동권을 보장하라"고 촉구했다... 후속 발표가 이어졌다.'
    summary = summarize_issue([single])
    assert all(marker not in summary for marker in ('"', "...", "…"))
    assert summary.endswith(".")
    assert analyze_tone([single]).count(".") <= 1

    other = _article("donga", "장애인 이동권 정책 발표", article_id="other")
    tone = analyze_tone([single, other])
    assert 1 <= tone.count(".") <= 4


def test_previous_coverage_is_only_inside_toggle_and_limited(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    current = _article(
        "ablenews",
        "제주 섭지코지 산책로 이동권 개선 인권위 권고 수용",
        article_id="current",
    )
    current.byline = "홍길동 기자"
    storage.upsert(current)
    for index in range(4):
        previous = _article(
            "theindigo",
            f"제주 섭지코지 산책로 이동권 개선 후속 {index}",
            article_id=f"previous-{index}",
        )
        previous.published_at = datetime(2026, 8, 14, index, tzinfo=UTC)
        previous.first_seen_at = previous.published_at
        previous.last_seen_at = previous.published_at
        storage.upsert(previous)
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    issue = document.sections[0].issues[0]
    assert 0 <= len(issue.previous_coverage) <= PREVIOUS_COVERAGE_MAX_ITEMS
    text = render_briefing_markdown(document, crpd_url=None)
    assert "### 동일 주제 이전 보도" not in text
    summary_index = text.index("<summary>동일 주제 이전 보도</summary>")
    assert summary_index < text.index("|", summary_index)
    if issue.previous_coverage:
        assert "| 자료 | 날짜·매체 |\n|---|---|" in text
    assert "이전 보도 요약" not in text
    assert "홍길동 기자" in text
    assert "| 언론사 | 기사 | 발행 |" not in text


def test_markdown_omits_reference_toggle_when_nothing_to_show() -> None:
    issue = BriefingIssue(
        title="장애인 이동권 보장 촉구",
        articles=[_article("hani", "장애인 이동권 보장 촉구")],
        summary="장애인단체는 이동권 보장을 요구했다.",
        tone_analysis="한겨레는 당사자 요구를 중심으로 보도했다.",
        previous_coverage=[],
    )
    document = BriefingDocument(
        report_date="2026-08-16",
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        overview="총평",
        telegram_summary="텔레그램 총평",
        sections=[BriefingSection("I. 장애정책·장애인운동", [issue])],
        source_failures=[],
    )
    text = render_briefing_markdown(document, crpd_url=None)
    assert "<details>" not in text
    assert "동일 주제 이전 보도" not in text
    assert "확인된 추가 자료 없음" not in text


def test_previous_coverage_proper_noun_matches_alone_but_category_term_does_not() -> None:
    """A shared proper noun names one event; a shared category term does not.

    "최중증" is as rare in the corpus as a proper noun like "hl만도", so document
    frequency cannot separate them. It still attaches unrelated reports to each
    other, which is why a category term now needs a second shared token.
    """

    election = _article(
        "beminor",
        "탈시설 당사자 조상지 씨, 지방선거 출마 경험을 말하다",
        article_id="election",
    )
    election.summary = "최중증 장애인 당사자의 정치 참여 경험을 다뤘다."
    unrelated = _article(
        "ablenews",
        "전신마비 김혁건, 박위 논란에 한 말",
        article_id="unrelated",
    )
    unrelated.summary = "최중증 장애인 인식을 둘러싼 방송 발언이 논란이 됐다."
    assert previous_coverage_for([election], [unrelated]) == []

    care_home = _article("beminor", "색동원 성폭력 사건 대책위, 인천시 면담 요구", article_id="cur")
    earlier = _article("ablenews", "인천시, 색동원 법인 설립허가 취소 처분", article_id="prev")
    earlier.summary = "인천시가 해당 법인의 설립허가를 취소했다고 밝혔다."
    matched = previous_coverage_for([care_home], [earlier])
    assert [item.url for item in matched] == [earlier.canonical_url]
    assert matched[0].match_rule == PREVIOUS_MATCH_PROPER_NOUN
    assert "색동원" in matched[0].matched_tokens


def test_previous_coverage_drops_reports_older_than_the_lookback_window() -> None:
    current = _article("beminor", "색동원 성폭력 사건 대책위, 인천시 면담 요구", article_id="cur")
    stale = _article("ablenews", "색동원 시설 운영 실태 점검", article_id="stale")
    stale.summary = "인천시가 해당 시설의 운영 실태를 점검했다고 밝혔다."
    old = current.published_at - timedelta(days=PREVIOUS_COVERAGE_MAX_AGE_DAYS + 1)
    stale.published_at = old
    stale.first_seen_at = old
    stale.last_seen_at = old
    assert previous_coverage_for([current], [stale]) == []


def test_previous_coverage_replaces_a_summary_fragment_with_a_title_sentence() -> None:
    current = _article("beminor", "색동원 성폭력 사건 대책위, 인천시 면담 요구", article_id="cur")
    fragment = _article("joongang", "색동원 피해자 지원 대책 발표", article_id="fragment")
    fragment.summary = "이어."
    matched = previous_coverage_for([current], [fragment])
    assert matched
    assert matched[0].comparison == "색동원 피해자 지원 대책 발표를 보도했다."


def test_previous_coverage_notes_stay_out_of_the_published_briefing(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    current = _article("beminor", "색동원 성폭력 사건 대책위, 인천시 면담 요구", article_id="cur")
    storage.upsert(current)
    earlier = _article("ablenews", "인천시, 색동원 법인 설립허가 취소 처분", article_id="prev")
    earlier.summary = "인천시가 해당 법인의 설립허가를 취소했다고 밝혔다."
    earlier.published_at = datetime(2026, 8, 14, tzinfo=UTC)
    earlier.first_seen_at = earlier.published_at
    earlier.last_seen_at = earlier.published_at
    storage.upsert(earlier)
    document = build_briefing(
        storage,
        topics_path=topics_path,
        start=datetime(2026, 8, 15, 0, tzinfo=UTC),
        end=datetime(2026, 8, 16, 0, tzinfo=UTC),
        report_date="2026-08-16",
    )
    notes = [note for note in document.editorial_notes if note.startswith("이전 보도 매칭")]
    assert any(PREVIOUS_MATCH_PROPER_NOUN in note for note in notes)
    rendered = render_briefing_markdown(document, crpd_url=None)
    assert "이전 보도 매칭" not in rendered
    assert "규칙=" not in rendered


def test_previous_coverage_notes_are_capped() -> None:
    issues = [
        BriefingIssue(
            title=f"이슈 {index}",
            articles=[],
            summary="요약.",
            tone_analysis="",
            previous_coverage=[
                PreviousCoverage("2026-08-01", f"이전 {index}", None, "비마이너", "요약.")
            ],
        )
        for index in range(PREVIOUS_COVERAGE_NOTE_LIMIT + 5)
    ]
    notes = previous_coverage_notes([BriefingSection("I. 장애정책·장애인운동", issues)])
    assert len(notes) == PREVIOUS_COVERAGE_NOTE_LIMIT + 1
    assert notes[-1].endswith("5건을 생략함")


def test_labor_subsection_orders_care_then_poverty_then_labor() -> None:
    from news_topic_monitor.briefing import _sort_labor_subsection

    labor_issue = BriefingIssue(
        title="사업장 산업재해 은폐 논란",
        articles=[],
        summary="중대재해가 산업안전보건법 위반으로 은폐됐다는 의혹이 제기됐다.",
        tone_analysis="노동조합은 진상규명을 요구했다.",
        keyword="산업재해 은폐",
    )
    poverty_issue = BriefingIssue(
        title="기초생활보장 부양의무자 기준 논란",
        articles=[],
        summary="빈곤층이 부양의무자 기준 탓에 생계급여를 받지 못한다는 지적이 나왔다.",
        tone_analysis="복지사각지대 해소가 필요하다는 지적이 이어졌다.",
        keyword="부양의무자 기준",
    )
    care_issue = BriefingIssue(
        title="요양보호사 처우 개선 요구",
        articles=[],
        summary="돌봄노동 종사자들이 활동지원사 처우 개선을 요구했다.",
        tone_analysis="공공돌봄 확대가 필요하다는 목소리가 나왔다.",
        keyword="요양보호사 처우",
    )

    ordered = _sort_labor_subsection([labor_issue, poverty_issue, care_issue])

    assert [issue.title for issue in ordered] == [
        "요양보호사 처우 개선 요구",
        "기초생활보장 부양의무자 기준 논란",
        "사업장 산업재해 은폐 논란",
    ]


def test_labor_subsection_sort_is_stable_within_the_same_group() -> None:
    from news_topic_monitor.briefing import _sort_labor_subsection

    first_labor = BriefingIssue(
        title="파업 첫 번째",
        articles=[],
        summary="노동조합이 파업을 예고했다.",
        tone_analysis="",
        keyword="파업",
    )
    second_labor = BriefingIssue(
        title="파업 두 번째",
        articles=[],
        summary="다른 사업장에서도 파업이 이어졌다.",
        tone_analysis="",
        keyword="파업",
    )

    ordered = _sort_labor_subsection([first_labor, second_labor])

    assert [issue.title for issue in ordered] == ["파업 첫 번째", "파업 두 번째"]


def _labor_issue(title: str, subsection: str | None = None) -> BriefingIssue:
    from news_topic_monitor.models import LaborSubsection

    return BriefingIssue(
        title=title,
        articles=[],
        summary="사안을 정리한 문장이다.",
        tone_analysis="",
        keyword=title,
        subsection=LaborSubsection(subsection) if subsection else None,
    )


def test_select_labor_issues_applies_subsection_and_total_caps_in_rank_order() -> None:
    from news_topic_monitor.briefing import select_labor_issues

    # Ranked most-important first; 노동 issues lead so the caps must trim within groups.
    ranked = (
        [_labor_issue(f"노동 {index}", "labor") for index in range(5)]
        + [_labor_issue(f"빈곤 {index}", "poverty") for index in range(5)]
        + [_labor_issue(f"소수자 {index}", "minority") for index in range(4)]
        + [_labor_issue(f"돌봄 {index}", "care") for index in range(6)]
    )

    selected = select_labor_issues(ranked)

    assert len(selected) == 8
    titles = [issue.title for issue in selected]
    # Walking in rank order fills 노동 2 → 빈곤 3 → 소수자 2 → 돌봄 1 before the total cap (8)
    # stops it, and the section is then presented 돌봄 → 빈곤 → 소수자 → 노동.
    assert titles == [
        "돌봄 0",
        "빈곤 0",
        "빈곤 1",
        "빈곤 2",
        "소수자 0",
        "소수자 1",
        "노동 0",
        "노동 1",
    ]


def test_classify_labor_subsection_recognises_minority_issues() -> None:
    from news_topic_monitor.labor_subsections import classify_labor_subsection
    from news_topic_monitor.models import LaborSubsection

    assert (
        classify_labor_subsection("고용허가제 이주노동자 사업장 변경 제한 위헌 소송")
        == LaborSubsection.MINORITY
    )
    assert classify_labor_subsection("차별금지법 제정 촉구 기자회견") == LaborSubsection.MINORITY
    # 동점이면 돌봄 > 빈곤 > 소수자 순이다.
    assert classify_labor_subsection("이주민 요양보호사 처우") == LaborSubsection.CARE
    assert classify_labor_subsection("동성로 상권 임금 체불") == LaborSubsection.LABOR


def test_select_labor_issues_allows_empty_and_never_pads() -> None:
    from news_topic_monitor.briefing import select_labor_issues

    assert select_labor_issues([]) == []
    assert [issue.title for issue in select_labor_issues([_labor_issue("노동 0", "labor")])] == [
        "노동 0"
    ]


def test_render_marks_empty_section_instead_of_leaving_a_bare_heading() -> None:
    from news_topic_monitor.briefing import BriefingDocument, render_briefing_markdown

    document = BriefingDocument(
        report_date="2026-08-16",
        start=datetime(2026, 8, 15, tzinfo=UTC),
        end=datetime(2026, 8, 16, tzinfo=UTC),
        overview="총평.",
        telegram_summary="요약.",
        sections=[BriefingSection("II. 노동·돌봄·빈곤·소수자", [])],
        source_failures=[],
        editorial_notes=[],
    )

    markdown = render_briefing_markdown(document, crpd_url=None)

    assert (
        "# II. 노동·돌봄·빈곤·소수자\n\n이번 브리핑에는 편집 기준에 따라 선정한 이슈가 없다."
        in markdown
    )


def _summary_document(section_title: str, summary: str) -> BriefingDocument:
    issue = BriefingIssue(
        title="합성 이슈 제목",
        articles=[],
        summary=summary,
        tone_analysis="",
        keyword="합성 키워드",
    )
    return BriefingDocument(
        report_date="2026-10-02",
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 10, 2, tzinfo=UTC),
        overview="총평.",
        telegram_summary="요약.",
        sections=[BriefingSection(section_title, [issue])],
        source_failures=[],
        editorial_notes=[],
    )


def test_previous_coverage_limit_matches_the_selection_limit() -> None:
    import pytest

    from news_topic_monitor.briefing import PreviousCoverage
    from news_topic_monitor.briefing_validation import BriefingValidationError

    def document_with(count: int) -> BriefingDocument:
        document = _summary_document("I. 장애정책·장애인운동", "서울시가 정류장을 정비한다.")
        document.sections[0].issues[0].previous_coverage = [
            PreviousCoverage(
                published=f"2026-09-{day:02d}",
                label=f"합성 이전 보도 {day}",
                url=None,
                outlet="합성",
                comparison="",
            )
            for day in range(1, count + 1)
        ]
        return document

    # 선정 단계가 PREVIOUS_COVERAGE_MAX_ITEMS건까지 붙이므로 검증은 같은 수까지 허용해야 한다.
    validate_briefing(document_with(PREVIOUS_COVERAGE_MAX_ITEMS))
    with pytest.raises(BriefingValidationError, match="이전 보도가"):
        validate_briefing(document_with(PREVIOUS_COVERAGE_MAX_ITEMS + 1))


def test_issue_summary_must_not_name_an_outlet_in_news_sections() -> None:
    import pytest

    from news_topic_monitor.briefing_validation import BriefingValidationError

    unattributed = "서울시가 정류장 120곳의 단차를 올해 안에 정비하겠다고 밝혔다."
    validate_briefing(_summary_document("I. 장애정책·장애인운동", unattributed))
    validate_briefing(_summary_document("II. 노동·돌봄·빈곤·소수자", unattributed))

    for title in ("I. 장애정책·장애인운동", "II. 노동·돌봄·빈곤·소수자"):
        for outlet in ("에이블뉴스", "매일노동뉴스", "한겨레"):
            with pytest.raises(BriefingValidationError, match=f"이슈 요약에 언론사\\({outlet}\\)"):
                validate_briefing(
                    _summary_document(title, f"{outlet}는 서울시가 정류장을 정비한다고 전했다.")
                )


def test_opinion_summary_is_exempt_from_the_outlet_rule_and_keeps_the_author_term() -> None:
    validate_briefing(
        _summary_document("III. 주요 칼럼", "필자는 이동권이 여가와 이어진 권리라고 주장했다.")
    )


def _keyword_issue(keyword: str) -> BriefingIssue:
    return BriefingIssue(
        title=f"{keyword} 관련 제목",
        articles=[],
        summary="요약이다.",
        tone_analysis="",
        keyword=keyword,
    )


def test_summary_particles_follow_the_last_syllable_of_the_keyword_list() -> None:
    from news_topic_monitor.briefing import build_overview, build_telegram_summary

    # 2026-10-08 텔레그램 요약이 "…통합돌봄 저조을 주요 후속 감시 대상으로"로 나갔다. 조사가
    # "을"로 고정돼 받침 없는 키워드가 끝에 오면 틀렸다.
    for keyword, particle in (("통합돌봄 저조", "를"), ("자립지원 예산 공백", "을")):
        sections = [BriefingSection("I. 장애정책·장애인운동", [_keyword_issue(keyword)])]
        telegram = build_telegram_summary(sections)
        assert f"{keyword}{particle} 주요 후속 감시 대상으로 정리하였다." in telegram

    for keyword, particle in (
        ("이동권 논쟁", "을"),
        ("택시 월급제 쟁점", "을"),
        ("복지 사각지대", "를"),
    ):
        sections = [BriefingSection("III. 주요 칼럼", [_keyword_issue(keyword)])]
        overview = build_overview(
            datetime(2026, 10, 7, tzinfo=UTC), datetime(2026, 10, 8, tzinfo=UTC), sections
        )
        assert f"주요 칼럼으로는 {keyword}{particle} 함께 소개한다." in overview


def test_previous_coverage_summary_skips_sentences_whose_quotes_were_removed() -> None:
    """Regression: 2026-10-30·31 — 인용을 지운 뒤 '…은 며 이라고 했다.'만 남은 문장이 뽑혔다."""

    from news_topic_monitor.briefing import _previous_coverage_summary

    article = _article("joongang", "진술 지원 제도 시행 10년 점검", article_id="quote")
    article.summary = (
        "\u2018입말\u2019 진술에 기대는 수사의 한계가 드러났다. "
        "대책위 사무국장은 “조사 직전에야 처음 만난다”며 “질문 몇 개로 끝난다”고 했다. "
        "전문가들은 조사 방식 개선이 필요하다고 지적했다. - 진술조력,조사"
    )
    assert _previous_coverage_summary(article) == "전문가들은 조사 방식 개선이 필요하다고 지적했다."

    keywords_only = _article("joongang", "진술 지원 제도 시행 10년 점검", article_id="tags")
    keywords_only.summary = "대책위는 “조사 직전에야 만난다”고 했다. - 발달장애인,진술조력"
    assert _previous_coverage_summary(keywords_only) == "진술 지원 제도 시행 10년 점검을 보도했다."

    unclosed = _article("joongang", "진술 지원 제도 시행 10년 점검", article_id="unclosed")
    unclosed.summary = "대책위는 “조사 방식이 문제다"
    assert _previous_coverage_summary(unclosed) == "진술 지원 제도 시행 10년 점검을 보도했다."
