# 출처별 공개 경로 검증 기록

검증 기준일은 2026-08-15(Asia/Seoul)이다. 사전 웹 조사 뒤
`KCILNewsMonitor/0.1 (+jhforwork24@gmail.com)` User-Agent로 프로젝트 live smoke와 최근
6시간 정식 수집을 실행했다. 연락처가 설정되기 전에는 프로젝트 수집기로 기사 본문을 요청하지
않았으며, 설정 후에도 robots 허용 표본만 확인했다.

| 출처 | 공식 경로 실제 결과 | `KCILNewsMonitor` live smoke | 본문 선택자 live 검증 |
|---|---|---|---|
| 조선일보 | robots·RSS·뉴스 sitemap 모두 200 | 통과: 두 발견 경로에서 URL 발견, 표본 기사 robots 허용 | 통과: `Fusion.globalContent.content_elements` |
| 중앙일보 | robots·최신·날짜별 sitemap 모두 200 | 통과: 두 sitemap에서 URL 발견, 표본 기사 robots 허용 | 통과: `#article_body` |
| 동아일보 | rss/www robots·통합 RSS·newsmap 모두 200 | 통과: 두 발견 경로에서 URL 발견, 표본 기사 robots 허용 | 통과: `.news_view` |
| 한겨레 | robots 200, `/rss/` 308→`/rss` 200, 최신기사 목록 200 | 통과: RSS 보조 경로·최신 목록에서 URL 발견, 표본 기사 robots 허용 | 통과: `article#renewal2023` |

프로젝트 live smoke는 2026-08-15 15:16~15:17 KST에 실행했으며 4건 모두 통과했다. 실제
최근 6시간 수집은 15:19~15:20 KST에 네 출처 모두 성공했다. 해당 실행의 발견 건수는 조선
149, 중앙 196, 동아 537, 한겨레 30이었고, 고정 시간창 안의 고유 기사 230건을 저장했다.

## 사전 조사에서 확인한 robots 정책

### 조선일보

공식 [`robots.txt`](https://www.chosun.com/robots.txt)의 `User-agent: *` 그룹은
`/main/bosi*`, `/test*`, `/search`, `/nsearch`, `/earlybird`, `/economy/realty/`를 금지하고
뉴스 sitemap을 선언한다. 별도 `GPTBot` 전면 금지 그룹은 우리 수집기 User-Agent에 적용되는
것으로 추정하지 않고, 실행 시 표준 robots 파서가 현재 User-Agent로 다시 판정한다.

- `https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml`
- `https://www.chosun.com/arc/outboundfeeds/news-sitemap/?outputType=xml`

### 중앙일보

공식 경로 후보는 다음과 같다.

- `https://www.joongang.co.kr/sitemap/latest-articles`
- `https://www.joongang.co.kr/sitemap/articles/YYYY/YYYYMMDD`

현재 robots는 여러 명시적 AI·수집 User-Agent에 `/`를 금지하고, 그 뒤 `User-agent: *`에
검색·구매·제보·일부 개별 기사 등 금지 경로를 둔다. `KCILNewsMonitor`는 별도 명시 그룹이
없어 `*` 그룹으로 판정되었고 공식 sitemap 및 smoke 표본 기사 URL은 허용되었다.

### 동아일보

공식 [`robots.txt`](https://www.donga.com/robots.txt)의 `User-agent: *` 그룹은
`/search`, `/news/search`, `/news/View`와 여러 대문자 서비스 경로 등을 금지하고
`https://www.donga.com/sitemap/donga-newsmap.xml`을 선언한다.

- `https://rss.donga.com/total.xml`
- `https://www.donga.com/sitemap/donga-newsmap.xml`

RSS host는 별도 origin이므로 프로젝트는 `https://rss.donga.com/robots.txt`도 독립적으로
확인한다. 가져오지 못하면 RSS 요청 자체를 중단하고 www origin의 newsmap은 별도로 처리한다.

### 한겨레

공식 [`robots.txt`](https://www.hani.co.kr/robots.txt)의 `User-agent: *` 그룹은
`/arti/PRINT/`, `/fortunes/result`, `/arti/PREVIEW/`, `/api/`, `/test/`를 금지한다.

- `https://www.hani.co.kr/arti?page=N`
- 보조 경로 후보: `https://www.hani.co.kr/rss/`

우리 정직한 User-Agent에는 최신기사 목록이 200을 반환했다. 실제 `__NEXT_DATA__`는
`props.pageProps.list` 배열이며 발행·수정시각 key는 `createDate`·`updateDate`, 섹션은
`section.name` 구조임을 확인해 파서와 fixture에 반영했다.

## robots 때문에 요청하지 않은 경로

smoke와 정식 수집은 발견된 허용 URL만 요청했다. 다음과 같은 현재 금지 경로는 요청하지 않았다.

- 조선일보: `/search`, `/nsearch`, `/economy/realty/` 등
- 중앙일보: `/search`, `/aisearch`, `/purchase/`, `/jebo/` 및 robots에 열거된 개별 기사 등
- 동아일보: `/search`, `/news/search`, `/news/View`, 대문자 서비스 경로 등
- 한겨레: `/arti/PRINT/`, `/arti/PREVIEW/`, `/api/`, `/test/` 등

이번 표본 기사 4건은 모두 robots 허용 경로였으므로 `blocked_by_robots` 표본은 없었다.

## live smoke 갱신 절차

```bash
export MONITOR_CONTACT='monitor@example.org'
pytest -m smoke -vv
```

각 출처에 대해 발견 URL 수, robots 판정, 본문 요청 여부, 선택자 검증 결과를 이 문서 표에
기록한다. 금지 또는 robots 확인 실패이면 기사 URL을 요청하지 않았음을 명시한다. 응답 HTML이나
기사 본문은 문서·fixture·로그에 복제하지 않는다.
