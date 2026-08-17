from __future__ import annotations

from datetime import UTC, datetime

from news_topic_monitor.briefing import (
    analyze_tone,
    build_briefing,
    crpd_articles,
    is_opinion,
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


def test_four_section_briefing_and_reverse_opinion(tmp_path, topics_path) -> None:
    storage = JsonlStorage(tmp_path)
    rows = [
        _article("hani", "장애인 이동권 보장 촉구", article_id="1"),
        _article(
            "labortoday",
            "돌봄노동자 임금 교섭",
            classification=Classification.IRRELEVANT,
            article_id="2",
        ),
        _article("kbs", "장애인 이동권 현장 리포트", article_id="3"),
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
        "III. 방송 뉴스 중 장애 주제",
        "IV. 주요 칼럼",
    ]
    assert document.sections[1].issues
    assert document.sections[3].issues
    text = render_briefing_markdown(document, crpd_url="https://notion.example/crpd")
    assert text.index("I. 장애정책") < text.index("II. 노동") < text.index("III. 방송")
    assert "<details>" in text
    assert "추가 자료 · 더 알아보기" in text
    assert "| 범주 | 자료 | 확인 쟁점 |" in text
    assert "### 이슈 요약·보도 논조" in text
    assert "### 기사 요약" not in text
    assert "### 보도 논조" not in text
    assert "| 언론사 | 기사 | 발행 |" not in text
    assert "KST" not in text
    assert "오늘의 변화" not in text
    assert "# 점검" not in text
    validate_briefing(document)


def test_opinion_detection() -> None:
    assert is_opinion(_article("donga", "[사설] 이동권은 시민권이다", section="오피니언"))
    assert not is_opinion(_article("donga", "이동권 집회 현장 보도"))
    article = _article("beminor", "평택 정신의료기관 입원환자 40명 집단 전원")
    article.summary = "사설구급차로 환자를 옮겼다는 보도다."
    assert not is_opinion(article)


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


def test_crpd_mapping_avoids_incidental_school_and_tour_bus_words() -> None:
    mapped = crpd_articles("인화학교 사건 이후 정신의료기관 입원자를 관광버스로 집단 전원했다.")
    assert not any(item.startswith("제24조") for item in mapped)
    assert not any(item.startswith("제30조") for item in mapped)
    assert any(item.startswith("제14조") for item in mapped)


def test_korean_particles_and_mbc_boilerplate_summary() -> None:
    column = _article("khan", "장애인 이동권 보도")
    assert analyze_tone([column]) == ""

    movement = _article("khan", "장애인 이동권 보장 촉구")
    assert analyze_tone([movement]).startswith("경향신문은 ")

    video = _article("mbc", "선수 명의로 보조금 챙긴 임원 벌금 약식기소")
    video.summary = (
        "지역사 채널의 동영상 링크 https://youtu.be/example "
        "#제주MBC #장애인체육회 무단 전재, 재배포 금지"
    )
    summary = summarize_issue([video])
    assert "무단 전재" not in summary
    assert video.title in summary


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
        "III. 방송 뉴스 중 장애 주제",
    ]
    assert "주요 칼럼" not in document.overview
    assert "칼럼 0" not in document.overview
    text = render_briefing_markdown(document, crpd_url=None)
    assert "IV. 주요 칼럼" not in text
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


def test_editorial_exclusions_section_assignment_and_crpd_order(tmp_path, topics_path) -> None:
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
    care_issue = next(
        issue
        for issue in document.sections[0].issues
        if "최중증 발달장애인 통합돌봄" in issue.title
    )
    care_labels = {reference.label for reference in care_issue.references}
    assert "발달장애인 권리보장 및 지원에 관한 법률" in care_labels
    assert "근로기준법" not in care_labels
    assert not any("아이돌봄" in label for label in care_labels)
    ordinance = next(issue for issue in document.sections[0].issues if "서울시의회" in issue.title)
    assert any("30268" in (item.url or "") for item in ordinance.previous_coverage)
    color_issue = next(issue for issue in document.sections[0].issues if "색동원" in issue.title)
    international_norms = [
        reference for reference in color_issue.references if reference.category == "국제 규범"
    ]
    assert any("thekdf.org" in (reference.url or "") for reference in international_norms)
    assert any(
        "uhr.humanrights.go.kr/pub/uhrstd/373" in (reference.url or "")
        for reference in international_norms
        if "일반논평" in reference.label
    )
    assert all(
        reference.category != "관련 연구 및 문서"
        for reference in color_issue.references
        if "일반논평" in reference.label
    )
    categories = list(dict.fromkeys(reference.category for reference in color_issue.references))
    requested_order = [
        "관련 단체 입장",
        "관련 연구 및 문서",
        "현행 제도",
        "국제 규범",
    ]
    assert categories == [category for category in requested_order if category in categories]


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
    columns = next(section for section in document.sections if section.title == "IV. 주요 칼럼")
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
    assert "### 이전 보도 참고" not in text
    assert text.index("<summary>추가 자료 · 더 알아보기</summary>") < text.index("| 이전 보도 |")
    assert "홍길동 기자" in text
    assert "| 언론사 | 기사 | 발행 |" not in text
