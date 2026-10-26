# 출처별 공개 경로 검증 기록

이 문서는 2026-08-15 시점의 검증 기록이다. 이후 KBS·MBC·SBS·JTBC 4개 방송사는 방송
프로그램 단위로 장애 보도를 정확히 추릴 방법이 없어 III절(방송)과 함께 프로젝트에서
완전히 제거되었다. 아래 표·수치는 당시 기록을 그대로 보존한 것이며 더는 현재 출처
목록을 반영하지 않는다.

검증일은 2026-08-15(Asia/Seoul)이다. 모든 요청은
`KCILNewsMonitor/0.1 (+jhforwork24@gmail.com)`을 사용했고, origin별 robots.txt를 먼저
확인했다. live smoke는 16개 출처 모두 기대 조건을 통과했으며 기사 본문은 저장하지 않았다.

| 출처 | 공식 발견 경로 | robots·발견 결과 | 본문 live 검증 |
|---|---|---|---|
| 조선일보 | 통합 RSS, news sitemap, 분야별 news-sitemap 9개, 기사피드 API(`/pf/api/v3/content/fetch/story-feed`) | 허용·URL 발견 | `Fusion.globalContent.content_elements` |
| 중앙일보 | latest 및 날짜별 sitemap | 허용·URL 발견 | `#article_body` |
| 동아일보 | 통합 RSS, newsmap | 두 origin 허용·URL 발견 | `.news_view` |
| 한겨레 | `/arti?page=N`, 보조 RSS | 허용·URL 발견 | `article#renewal2023` |
| 경향신문 | `/sitemap/latest-articles.xml` | 허용·URL 발견 | `#articleBody` |
| 오마이뉴스 | `/NWS_Web/View/latestnews.aspx` news sitemap | 허용·URL 발견 | `[itemprop='articleBody']` |
| 프레시안 | `/api/v3/site/rss/news`(최근 25건), `/sitemap/news.xml`(약 2.5일, 제목·시각 포함) | 허용·URL 발견 | `.article_body` |
| 참세상 | 공식 origin | robots.txt HTTP 404, 전체 요청 안전 중단 | 요청하지 않음 |
| 매일노동뉴스 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 미디어스 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 비마이너 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 에이블뉴스 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 더인디고 | `/wp-json/wp/v2/posts` metadata fields | 허용·URL 발견 | `.td-post-content` |
| KBS | `/sitemap/recentNewsList.xml` | 허용·URL 발견 | redirect 재검사 후 `.detail-body` |
| MBC | `imnews.imbc.com` | `User-agent: * Disallow: /`, 웹 발견·본문 요청 차단 | 요청하지 않음 |
| SBS | `/news/sitemapRSS.do` | 허용·URL 발견 | `[itemprop='articleBody']` |
| JTBC | `/sitemaps/latest-articles` | 허용·URL 발견 | 서버 본문 구조 미확인, 메타데이터만 |

2026-08-15 기존 16개 출처 live smoke 결과는 `16 passed`(75.94초)였다. MBC는 금지 판정,
참세상은 robots 확인 실패 중단, JTBC는 본문 미추정이 각각 기대 성공 조건이었다. 미디어스는
이후 보완 출처로 추가하고 공식 sitemap, robots 허용, 실제 기사의 본문 선택자를 별도로
확인했다.

## MBC 공식 YouTube 보완 경로

위 live smoke와 48시간 수치는 YouTube API 보완 어댑터 도입 전의 기준선이다. 이후 MBC 웹
경로는 계속 요청하지 않되, [MBCNEWS 공식 채널](https://www.youtube.com/channel/UCF4Wxdo3inmxP-Y59wXDsFw)을
대상으로 다음 두 [YouTube Data API](https://developers.google.com/youtube/v3/docs) 발견 경로를
결합하였다.

- 조사기간을 지정한 `search.list` 장애 의제 키워드 묶음 검색
- `channels.list`로 공식 업로드 playlist를 확인한 뒤 `playlistItems.list`를 최신순으로 순회하고,
  제목·공개 설명에서 넓은 장애 의제 표현을 로컬 교차확인

API 키는 URL 매개변수가 아니라 `x-goog-api-key` 헤더로만 전달하며, robots.txt 확인 요청과
cross-origin 리다이렉트에는 붙이지 않는다. API 응답에서는 공식 채널 ID가 일치하는 영상의
ID·제목·공개 설명 일부·발행시각·공개 watch URL만 사용하고 영상·자막·웹 본문은 요청하지 않는다.
마지막 API 확인 후 28일이 지난 저장 레코드는 `videos.list`로 갱신하고, 성공 응답에서 더는
반환되지 않는 정확한 ID의 현재 캐시는 제거한다.
오프라인 합성 fixture와 헤더 유출 방지 시험은 완료했으나, repository secret은 로컬에서 읽을
수 없으므로 API live smoke는 변경사항을 GitHub Actions에서 실행한 뒤 이 문서에 별도로 결과를
추가해야 한다.

## 48시간 실제 백필

같은 날 16개 출처 전체 48시간 백필은 5분 13초가 걸렸고 14개 출처가 성공했다. 저장소 전체
고유 메타데이터는 3,756건이 되었으며, 실행 구간의 주요 수치는 다음과 같았다.

| 출처 | 발견 | 기간 내 처리 | 신규 | 관련 | 검토 | 본문 확인 |
|---|---:|---:|---:|---:|---:|---:|
| 조선일보 | 149 | 149 | 28 | 0 | 0 | 0 |
| 중앙일보 | 674 | 452 | 428 | 3 | 0 | 3 |
| 동아일보 | 541 | 541 | 498 | 0 | 0 | 0 |
| 한겨레 | 285 | 273 | 255 | 0 | 1 | 1 |
| 경향신문 | 176 | 176 | 176 | 2 | 0 | 2 |
| 오마이뉴스 | 174 | 174 | 174 | 0 | 0 | 0 |
| 프레시안 | 25 | 25 | 25 | 0 | 0 | 0 |
| 참세상 | 0 | 0 | 0 | 0 | 0 | 0 |
| 매일노동뉴스 | 100 | 28 | 28 | 0 | 0 | 0 |
| 비마이너 | 100 | 8 | 8 | 4* | 0 | 4* |
| 에이블뉴스 | 100 | 33 | 33 | 31* | 0 | 31* |
| 더인디고 | 9 | 9 | 9 | 8 | 0 | 8 |
| KBS | 3,317 | 1,644 | 1,644 | 5 | 0 | 4 |
| MBC | 0 | 0 | 0 | 0 | 0 | 0 |
| SBS | 100 | 99 | 99 | 0 | 0 | 0 |
| JTBC | 117 | 116 | 116 | 0 | 0 | 0 |

별표 수치는 장애언론 암묵 표현 회귀규칙을 보완한 뒤 세 장애언론만 재실행한 최종 판정이다.
KBS 1건은 후보였으나 `.detail-body`가 빈 구조여서 메타데이터 판정과 구조 경고만 남겼다.
오마이뉴스 sitemap의 `star.ohmynews.com` URL은 허용 host 목록 밖이라 요청하지 않고 구조 경고를
기록했다.

## robots 때문에 수행하지 않은 접근

- MBC는 robots의 `User-agent: * Disallow: /` 때문에 iMBC 홈페이지·발견 경로·기사 URL을
  요청하지 않았다. 별도 Google API origin에서는 MBCNEWS 공식 채널의 문서화된 메타데이터
  엔드포인트만 사용한다.
- 참세상은 `/robots.txt`가 404여서 허용으로 추정하지 않고 홈페이지·기사 URL을 요청하지 않았다.
  (2026-09-25 갱신: 사용자가 404를 "robots.txt 없음"으로 해석하는 출처별 opt-in을 승인했다.
  2026-10-07: 참세상이 www.newscham.net에서 newscham.net으로 이전해 robots.txt(200,
  `/wp/wp-admin/`만 금지)를 게시했고, opt-in은 철회했다. 같은 날 live로 확인한 새 구조는
  `/all-articles/?_paged=N` 목록의 `div.article-loop_default div.gb-loop-item`(`h3`,
  `a.link`, `p.summary`, `div.author`, `div.date`, `div.taxonomy a`)와 기사 페이지의
  `article.post-content div.ep-single-content`다. 2026-10-09 live 확인: 별도 목록
  `/all-articles/curation/?_paged=N`(큐레이션, 같은 마크업, 하루 1건꼴)에만 있는 기사가 있어
  (130022·130013·130006) 어댑터가 1~2쪽을 추가 발견 경로로 수집한다. 아래 괄호는 철회 전
  기록이다.
  같은 날 live로 `/articles/` 목록의 `section.mainContents article.figure`, `h3 a`,
  `time.pubdate`(KST 표시시각), `p.summary`, `address.author`와 기사 페이지의
  `article#news-article-post div#news-article-content` 구조를 확인했다. `/rss/`는 빈 응답,
  `/rss.xml`·`/sitemap.xml`은 404였다. 본문·HTML은 저장하지 않았다.)
- 각 매체 robots에 명시된 검색·로그인·구매·관리·API 금지 경로는 사용하지 않았다.
- JTBC의 클라이언트 API나 브라우저 렌더링을 추정·호출하지 않았다.
- 오마이뉴스의 별도 `star.ohmynews.com` origin은 어댑터 허용 host 밖이므로 요청하지 않았다.

## 갱신 절차

```bash
export MONITOR_CONTACT='monitor@example.org'
pytest -m smoke -vv
```

선택자를 바꾸기 전에는 실제 허용 기사에서 존재·비어 있지 않음을 다시 확인한다. 금지 또는
robots 확인 실패이면 기사 URL을 요청하지 않았음을 명시한다. 응답 HTML이나 기사 본문은
문서·fixture·로그에 복제하지 않는다.

## 프레시안 뉴스 사이트맵 추가 (2026-10-19)

- 배경: 프레시안 RSS는 최근 25건만 제공한다. 2026-10-14·10-18에 `robots.txt` 확인 실패로 수집이
  중단된 뒤 복구된 시점의 RSS는 8~9시간 전 기사까지 닿지 않아 그 시간대 기사를 놓쳤다
  (health 기록의 직전 정상 수집 최신 기사와 복구 시점 목록 최고(最古) 기사 사이 공백).
- 확인(live, 같은 날): `robots.txt`의 `User-agent: *`는 `/admin/`·댓글·검색(`/pages/search`)만 금지하고
  `/sitemap/news.xml`은 막지 않는다. `Sitemap: https://www.pressian.com/sitemap.xml`이 선언돼 있고
  색인 아래 `/sitemap/news.xml`은 Google News 형식(`news:publication_date`, `news:title`)으로 117건,
  약 2.5일분이다. 기존 sitemap 파서(`parse_xml_feed`)가 그대로 처리한다. 월별
  `/sitemap/articles/YYYY-MM.xml`은 제목이 없어 쓰지 않는다. 본문·HTML은 저장하지 않았다.
- 한계: 이 경로도 같은 origin이므로 `robots.txt`를 확인하지 못한 회차에는 함께 중단된다. 줄이는 것은
  복구 뒤 RSS 25건 제한으로 놓치던 기사다. 네이버·다음 뉴스는 `robots.txt`가 전체 금지(네이버는 AI
  학습·검색증강 사용도 금지)이거나(`search.daum.net`) `robots.txt`가 없어(`news.daum.net`,
  `v.daum.net`) 사용하지 않는다. Naver API Hub는 계속 누락 탐지에만 쓴다.

## 조선일보 기사피드 API (2026-10-26 확인)

조선일보 통합 RSS·news-sitemap·분야 sitemap은 모두 최근 100건만 담는다. 2026-10-26 실측으로
낮 시간대에는 RSS가 1~2시간, 분야 sitemap이 경제 약 8시간·스포츠 약 2시간 분량이어서, 수집
회차가 비면 그 사이 기사를 놓친다. 같은 날 대조 구간(2026-10-24 19:00~10-26 01:00 KST)에서
기사피드 639건 중 329건이 저장분에 없었다.

- robots.txt(www.chosun.com)는 `/pf/api`를 막지 않는다. 수집기 User-Agent로 `robots_decision`이
  허용으로 판정함을 직접 확인했다.
- 요청: `/pf/api/v3/content/fetch/story-feed?query=<JSON>&_website=chosun&filter=<필드>`.
  `query`는 `includeContentTypes`·`excludeContentTypes`·`includeSections`(`/national` 등)·`size`
  (100)·`offset`이다. `filter`가 없으면 한 쪽이 약 8MB(본문 포함)이므로 반드시 붙인다(있으면 약
  34KB).
- 피드는 표시 시각(`display_date`) 내림차순이다. 수정으로 표시 시각이 갱신된 옛 기사가 앞쪽에
  섞이므로 넘김 중단은 표시 시각으로 판단하고, 기사의 `published_at`은 `first_publish_date`를
  쓴다. 일본어판(`/jp/`)·영문판(`/english/`)은 저장하지 않는다.
- 브리핑 대상이 아닌 스포츠·연예 분야는 이 경로에서 뺀다(분야 sitemap으로는 계속 수집한다).
- 일시 HTTP 503이 있었다. 공통 HTTP 클라이언트의 재시도·대기를 따르고, 실패해도 기존 경로는
  그대로 쓴다(health의 `errors`에 남는다).
