from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.briefing import (
    BriefingDocument,
    BriefingIssue,
    BriefingSection,
    analyze_tone,
    build_briefing,
    is_opinion,
    issue_analysis_text,
    previous_coverage_for,
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
        "II. 노동·돌봄·빈곤",
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
        "II. 노동·돌봄·빈곤",
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


def test_previous_coverage_is_only_inside_toggle_and_limited_to_three(
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
    assert 0 <= len(issue.previous_coverage) <= 3
    text = render_briefing_markdown(document, crpd_url=None)
    assert "### 동일 주제 이전 보도" not in text
    summary_index = text.index("<summary>동일 주제 이전 보도</summary>")
    assert summary_index < text.index("|", summary_index)
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
