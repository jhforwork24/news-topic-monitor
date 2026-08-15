# 출처별 공개 경로 검증 기록

검증일은 2026-08-15(Asia/Seoul)이다. 모든 요청은
`KCILNewsMonitor/0.1 (+jhforwork24@gmail.com)`을 사용했고, origin별 robots.txt를 먼저
확인했다. live smoke는 16개 출처 모두 기대 조건을 통과했으며 기사 본문은 저장하지 않았다.

| 출처 | 공식 발견 경로 | robots·발견 결과 | 본문 live 검증 |
|---|---|---|---|
| 조선일보 | 통합 RSS, news sitemap | 허용·URL 발견 | `Fusion.globalContent.content_elements` |
| 중앙일보 | latest 및 날짜별 sitemap | 허용·URL 발견 | `#article_body` |
| 동아일보 | 통합 RSS, newsmap | 두 origin 허용·URL 발견 | `.news_view` |
| 한겨레 | `/arti?page=N`, 보조 RSS | 허용·URL 발견 | `article#renewal2023` |
| 경향신문 | `/sitemap/latest-articles.xml` | 허용·URL 발견 | `#articleBody` |
| 오마이뉴스 | `/NWS_Web/View/latestnews.aspx` news sitemap | 허용·URL 발견 | `[itemprop='articleBody']` |
| 프레시안 | `/api/v3/site/rss/news` | 허용·URL 발견 | `.article_body` |
| 참세상 | 공식 origin | robots.txt HTTP 404, 전체 요청 안전 중단 | 요청하지 않음 |
| 매일노동뉴스 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 비마이너 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 에이블뉴스 | `/sitemap.xml` | 허용·URL 발견 | `#article-view-content-div` |
| 더인디고 | `/wp-json/wp/v2/posts` metadata fields | 허용·URL 발견 | `.td-post-content` |
| KBS | `/sitemap/recentNewsList.xml` | 허용·URL 발견 | redirect 재검사 후 `.detail-body` |
| MBC | `imnews.imbc.com` | `User-agent: * Disallow: /`, 발견 요청 차단 | 요청하지 않음 |
| SBS | `/news/sitemapRSS.do` | 허용·URL 발견 | `[itemprop='articleBody']` |
| JTBC | `/sitemaps/latest-articles` | 허용·URL 발견 | 서버 본문 구조 미확인, 메타데이터만 |

2026-08-15 live smoke 결과는 `16 passed`(75.94초)였다. MBC는 금지 판정, 참세상은 robots
확인 실패 중단, JTBC는 본문 미추정이 각각 기대 성공 조건이었다.

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

- MBC는 robots의 `User-agent: * Disallow: /` 때문에 홈페이지·발견 경로·기사 URL을 요청하지 않았다.
- 참세상은 `/robots.txt`가 404여서 허용으로 추정하지 않고 홈페이지·기사 URL을 요청하지 않았다.
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
