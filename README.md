# KCIL News Topic Monitor

13개 인쇄·디지털 매체와 KBS·MBC·SBS·JTBC의 공식 공개 RSS·뉴스 사이트맵·최신기사 목록,
MBCNEWS 공식 YouTube 채널 API를 주기적으로 확인하고, 발견 기사·영상 메타데이터를 중복 없이
기록한 뒤 장애인권과
노동·돌봄·빈곤 의제를 규칙 기반으로 판별하는 Python 3.12 프로젝트이다. 개인 PC를 켜 두거나
화면을 원격 조작하지 않으며, GitHub Actions만으로 무료 초기 운영이 가능하다.

이 시스템은 장애인을 시혜와 보호의 대상으로 환원하지 않고 권리의 주체이자 동등한 시민,
노동자로 보는 관점에서 관련 의제를 포괄적으로 발견하기 위해 설계되었다. 자동 판별은 운동과
정책 판단을 대체하지 않으며, 경계 사례는 반드시 사람의 검토 대상으로 남긴다.

## “전체 기사 모니터링”의 정확한 의미

여기서 “전체 기사”란 대상 매체의 **robots.txt가 허용하는 공식 RSS·사이트맵·최신기사
목록에 현재 노출되는 모든 기사 URL**을 뜻한다. 유료기사·로그인 영역·robots.txt 금지 경로를
우회해 사이트 전체를 긁는다는 뜻이 아니다. 발견 기사에는 가능한 범위에서 URL, 제목, 섹션,
발행·수정 시각, 공개 요약과 판별 결과만 저장한다.

모든 기사에 제목·요약·섹션 1차 판별을 적용한다. 넓은 후보 임계값을 넘은 기사에 한해서만,
해당 URL이 그 시점의 robots.txt에서 허용되고 `MONITOR_CONTACT`가 설정된 경우 본문을
일시적으로 내려받아 2차 판별한다. 본문은 판별 직후 폐기하며 저장소에는 정규화 텍스트의
SHA-256 해시, 일치어, 점수, 판정 근거만 남는다.

## 출처와 발견 경로

| 출처 | 기본 발견 경로 | 본문 후보 구조 |
|---|---|---|
| 조선일보 | 통합 RSS, Google News 사이트맵 | `script#fusion-metadata`의 `Fusion.globalContent.content_elements` |
| 중앙일보 | 최신기사 사이트맵, 조사기간의 날짜별 사이트맵 | `#article_body` |
| 동아일보 | 통합 RSS, 뉴스맵 | `.news_view` |
| 한겨레 | `https://www.hani.co.kr/arti?page=N` | `article#renewal2023` |
| 경향신문 | 최신기사 news sitemap | `#articleBody` |
| 오마이뉴스 | 공식 최신기사 news sitemap | `[itemprop='articleBody']` |
| 프레시안 | 공식 최신뉴스 RSS API | `.article_body` |
| 참세상 | robots.txt 확인 실패로 안전 중단 | 요청하지 않음 |
| 매일노동뉴스 | news sitemap | `#article-view-content-div` |
| 미디어스 | 공식 sitemap | `#article-view-content-div` |
| 비마이너 | news sitemap | `#article-view-content-div` |
| 에이블뉴스 | news sitemap | `#article-view-content-div` |
| 더인디고 | 본문을 제외한 공식 WordPress posts API | `.td-post-content` |
| KBS | recentNewsList news sitemap | `.detail-body` |
| MBC | YouTube Data API의 MBCNEWS 공식 채널 키워드 검색 + 업로드 목록 교차확인 | 영상 메타데이터만 저장, iMBC 본문은 요청하지 않음 |
| SBS | 공식 sitemap RSS | `[itemprop='articleBody']` |
| JTBC | latest-articles news sitemap | 서버 렌더링 선택자 미확인, 메타데이터만 저장 |

선택자는 코드에 구현되어 있지만 실제 사이트 구조는 바뀔 수 있다. `tests/fixtures/`는 파서의
최소 계약을 검증하고, 현재 구조 여부는 연락처를 설정한 live smoke test로만 확인한다.
확인되지 않은 선택자를 “작동 중”이라고 간주해서는 안 된다.
현재 조사 기록과 live 검증 대기 항목은
[`docs/source-verification.md`](docs/source-verification.md)에 분리해 기록한다.

## 설치와 로컬 시험

Python 3.12에서 다음을 실행한다.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install '.[dev]'
pytest -m 'not smoke'
ruff check .
ruff format --check .
```

일반 시험은 네트워크를 사용하지 않는다. live smoke test는 실제 공식 경로에 요청하므로 공개
연락처를 반드시 지정해야 한다. MBC live smoke test까지 실행하려면 `YOUTUBE_API_KEY`도
환경변수로 지정한다.

```bash
export MONITOR_CONTACT='monitor@example.org'
pytest -m smoke -vv
```

`MONITOR_CONTACT`는 연락 가능한 이메일 주소 또는 공개 HTTP(S) URL이어야 한다. 수집기의
User-Agent는 `KCILNewsMonitor/0.1 (+${MONITOR_CONTACT})`이다. 값이 없거나 형식이 잘못되면
수집 명령은 네트워크 요청 전에 종료한다. `.env` 파일 자동 로딩에는 의존하지 않는다.

최근 6시간 수집과 48시간 백필 예시는 다음과 같다.

```bash
MONITOR_CONTACT='monitor@example.org' news-topic-monitor collect --since-hours 6
MONITOR_CONTACT='monitor@example.org' news-topic-monitor backfill --hours 48
news-topic-monitor report --date 2026-08-15
news-topic-monitor briefing --date 2026-08-15
news-topic-monitor publish-notion --date 2026-08-15 --dry-run
```

수동 조사기간은 UTC 오프셋을 포함한 ISO 8601로 지정한다. 시작은 포함하고 종료는 제외한다.

```bash
news-topic-monitor collect \
  --start '2026-08-14T23:00:00Z' \
  --end '2026-08-15T05:00:00Z'
news-topic-monitor report \
  --date 2026-08-15 \
  --start '2026-08-14T00:00:00Z' \
  --end '2026-08-15T00:00:00Z'
```

## GitHub Actions 배포

1. 이 프로젝트를 GitHub 저장소의 기본 브랜치에 push한다.
2. 저장소 **Settings → Secrets and variables → Actions → Variables**에
   `MONITOR_CONTACT`를 등록한다. 비밀값이 아니라 공개 연락처이므로 Repository variable을
   사용한다.
3. 같은 화면의 **Secrets**에 `YOUTUBE_API_KEY`를 Repository secret으로 등록한다. 키는
   URL·로그·저장 데이터에 쓰지 않고 `x-goog-api-key` 요청 헤더로만 전달한다.
4. Actions 탭에서 `Collect news metadata`를 한 번 수동 실행하고 `health/latest.json`과
   `data/articles/` 변경이 커밋되는지 확인한다.
5. 이어 `Daily backfill`과 `Daily report`를 수동 실행해 권한과 보고서 생성을 확인한다.
6. 노션 발행을 쓸 때만 아래 노션 변수를 등록하고 통합 secret을 공유한 뒤
   `NOTION_PUBLISH_ENABLED=true`로 전환한다. 비활성 상태에서는 예약 job이 안전하게 skip된다.
7. production 발행에는 repository secret `OPENAI_API_KEY`, repository variable
   `OPENAI_EDITOR_ENABLED=true`, `PUBLICATION_OWNER=github_editorial_publish`를 등록한다.
   독립 누락 탐지를 활성화하려면 `NAVER_API_HUB_CLIENT_ID`와
   `NAVER_API_HUB_CLIENT_SECRET`도 secret으로 등록한다. Naver 미설정은 명시적
   `DEGRADED`로 남지만 원문 검증 등급을 올리지 않는다.

워크플로는 다음과 같다.

- `.github/workflows/collect.yml`: `17 2-23/3 * * *`(UTC), 3시간 간격으로 최근 6시간을
  재확인하며 최종 사전 실행은 08:17 KST에 시작
- `.github/workflows/backfill.yml`: `20 22 * * *`(UTC), 매일 07:20 KST에 최근 48시간 재확인
- `.github/workflows/report.yml`: `2 0 * * *`(UTC), 매일 09:02 KST에 전날 09:00부터
  당일 09:00 KST까지 보고
- `.github/workflows/editorial-publish.yml`: `5 0 * * *`(UTC), 매일 09:05에 48시간
  증거를 재수집하고 GPT 판별·편집·독립 감사, 진행형 사건 최종상태 재검증, publish gate를
  거쳐 유일하게 Notion 최종 발행을 수행함. 48시간은 지연 발견 범위이고, 전수 본문 확인은
  24시간 보고 구간으로 제한하여 10:00 감독 작업 전 완료 여유를 확보함
- `.github/workflows/editorial-queue.yml`: 예약 없음. 사용자가 명시적으로
  `CHAT_EDITORIAL_SHADOW_ENABLED=true`로 둔 수동 그림자 시험에서만 실행함
- `.github/workflows/publish-notion.yml`: 예약 없음. `PUBLICATION_OWNER=deterministic_fallback`인
  수동 복구 실행에서만 사용함
- `.github/workflows/ci.yml`: push·PR에서 오프라인 pytest와 ruff 실행

데이터 작성 워크플로는 동일한 `concurrency` 그룹을 사용해 동시 커밋을 막고,
`contents: write`만 부여한다. 변경이 있을 때만 커밋하며 push 실패 시 최신 브랜치를 rebase한
뒤 최대 3회 제한적으로 재시도한다. 충돌을 자동 덮어쓰지는 않는다.

GitHub의 예약 실행은 정각에 정확히 시작된다고 보장되지 않는다. 따라서 시간 경계는 실행시각이
아니라 기사 `published_at`으로 계산하고, 다음 6시간 중복 수집과 일일 48시간 백필로 지연·누락을
회복한다.

## 설정

환경변수 예시는 [`config/env.example`](config/env.example)에 있다.

| 이름 | 필수 | 기본값 | 설명 |
|---|---:|---:|---|
| `MONITOR_CONTACT` | 예 | 없음 | User-Agent에 넣을 공개 이메일 또는 URL |
| `REQUEST_INTERVAL_SECONDS` | 아니오 | `2.0` | 도메인별 요청 최소 간격. 2초 미만 값은 2초로 올림 |
| `CONNECT_TIMEOUT_SECONDS` | 아니오 | `10` | 연결 제한시간 |
| `READ_TIMEOUT_SECONDS` | 아니오 | `20` | 읽기 제한시간 |
| `MAX_RETRIES` | 아니오 | `2` | 제한적 재시도 횟수 |
| `MAX_DISCOVERY_CHILDREN` | 아니오 | `20` | sitemap index 하위 요청 상한 |
| `HANI_MAX_PAGES` | 아니오 | `50` | 한겨레 최신기사 최대 순회 페이지 |
| `YOUTUBE_API_KEY` | MBC 확인 시 | 없음 | YouTube Data API 키. GitHub Actions repository secret으로만 저장 |
| `NOTION_DATA_SOURCE_ID` | 노션 사용 시 | 없음 | 브리핑 대상 data source UUID |
| `NOTION_REPORTS_DATA_SOURCE_ID` | 아니오 | 없음 | 편집·분류·출처 점검 및 발행 실패 보고사항 data source UUID |
| `NOTION_CRPD_REFERENCE_URL` | 아니오 | 없음 | CRPD 조문별 통합참조표 URL |
| `NOTION_PUBLISH_ENABLED` | 아니오 | `false` 취급 | `true`일 때만 발행 job 실행 |
| `PUBLICATION_OWNER` | production | 없음 | `github_editorial_publish`일 때만 예약 최종 발행 |
| `OPENAI_EDITOR_ENABLED` | 아니오 | `false` 취급 | `true`일 때 임시 증거 수집과 GPT 편집 발행 job 실행 |
| `OPENAI_EDITOR_MODEL` | 아니오 | `gpt-5.6` | Responses API 편집 모델 |
| `OPENAI_AUDITOR_MODEL` | 아니오 | 편집 모델 | 편집 근거를 받지 않는 독립 감사 모델 |
| `OPENAI_EDITOR_CHUNK_SIZE` | 아니오 | `20` | 1차 판별 한 요청의 기사 수 |
| `OPENAI_EDITOR_MAX_CANDIDATES` | 아니오 | `360` | GPT에 전달하는 확인 가능 후보 상한 |
| `OPENAI_EDITOR_FINAL_CANDIDATES` | 아니오 | `80` | 2차 최종 편집 후보 상한 |
| `OPENAI_EDITOR_EVIDENCE_CHARS` | 아니오 | `5000` | 후보별 일시 전달 근거 글자 상한 |
| `NAVER_API_HUB_CLIENT_ID` | gap detector | 없음 | Naver API Hub client ID, repository secret |
| `NAVER_API_HUB_CLIENT_SECRET` | gap detector | 없음 | Naver API Hub client secret, repository secret |
| `CHAT_EDITORIAL_SHADOW_ENABLED` | 아니오 | `false` | 수동 ChatGPT 대기열 그림자 시험만 허용 |
| `CHAT_EDITORIAL_MAX_CANDIDATES` | 아니오 | `180` | ChatGPT 예약 작업에 제공할 검증 후보 상한 |
| `CHAT_EDITORIAL_CHUNK_SIZE` | 아니오 | `24` | Notion 임시 대기열 한 페이지의 후보 수(최대 24) |
| `CHAT_EDITORIAL_EVIDENCE_CHARS` | 아니오 | `1600` | 후보별 임시 확인 근거 글자 상한(최대 1800) |
| `CHAT_EDITORIAL_BODY_LIMIT_PER_SOURCE` | 아니오 | `60` | 보고 구간에서 출처별로 넓게 확인할 일반 기사 본문 상한. 규칙 후보는 상한 밖에서도 확인 |

`NOTION_TOKEN`은 환경 예제나 저장소 변수에 두지 않고 GitHub Actions repository secret으로만
저장한다. 토큰을 만든 내부 통합에 브리핑 테스트 데이터베이스와 보고사항 데이터베이스를
명시적으로 연결해야 한다.

`YOUTUBE_API_KEY`도 repository secret으로만 저장한다. MBC 어댑터는 이 키를 쿼리 문자열에
넣지 않고 `youtube.googleapis.com` 동일 origin 요청 헤더에만 전달한다. robots.txt 요청과
cross-origin 리다이렉트에는 키를 전달하지 않는다. 키가 없으면 MBC만
`configuration_missing`, API 할당량이 소진되면 `quota_exceeded`, 일부 발견 경로만 실패하면
`partial`로 기록하며 이를 보도 부재로 처리하지 않는다.

[YouTube API 개발자 정책](https://developers.google.com/youtube/terms/developer-policies)의
비인가 API 데이터 30일 제한을 지키기 위해 MBC 현재 메타데이터 캐시는 마지막 확인 후 28일이
지나기 전에 `videos.list`로 자동 갱신한다. API가 더는 돌려주지 않는 정확한 영상 ID의 현재 캐시
레코드는 제거하고, 당시 시점이 명시된 과거 일일보고는 역사 자료로 유지한다. 저장·표시·삭제
범위와 배포 운영자의 의무는 [`docs/youtube-api-use.md`](docs/youtube-api-use.md)에 정리한다.

GPT 편집 경로는 GitHub runner의 임시 SQLite에만 본문 근거를 보관한다. 1차 요청은 기사별
포함·제외·섹션을 판정하고, 2차 요청은 동일 사안을 통합해 초안을 만든다. 3차 독립 감사는
1차 편집기의 판정 이유를 받지 않고 초안과 원근거만 대조한다. 세 요청 모두 Structured Outputs와
`store=false`를 사용한다. 이어 공식 출처를 발행 직전에 다시 수집해 진행형 사건의 철수·종료·
합의·타결·답변·수용·철회·보류·재개·속보·후속 상태를 검사한다.

GPT가 입력에 없는 기사 ID를 만들거나, 확인하지 못한 기사를 고르거나, 같은 기사를 중복 배치하거나,
섹션·요약·논조 규칙을 어기면 브리핑과 노션 발행을 중단한다. 임시 DB와 본문은 명령 종료 때
삭제하며 저장소에는 기사 본문 없이 `evidence/YYYY-MM-DD.json` provenance와 health만 남긴다.
publish gate는 장애언론 census 3/3 COMPLETE, 각 이슈의 지정매체 역검색 9/9 또는 명시적
DEGRADED, 핵심기사 본문 확인, final-state COMPLETE, 독립 감사 fatal error 0, 미분류 실패 0을
검사한다. 하나라도 충족하지 못하면 Notion 최종 브리핑을 만들지 않고 `브리핑 보고사항`에 원인·
대체경로·결과·다음 조치를 남긴다. Naver 원문 URL은 결정론적 수집 URL 집합과 대조하며, 장애언론
census에 없는 잠재 누락이 발견되면 검색결과를 원문으로 간주하지 않고 gate를 차단한다.

10:00 ChatGPT 예약 작업은 production 전환 뒤 수집·편집·Notion 발행을 반복하지 않는다. 전환
기간에는 오늘자 GitHub gate와 Notion 발행 성공이 확인되면 즉시 감독 모드로 끝내고, 새 writer가
아직 활성화되지 않은 날에만 기존 발행을 유지한다. 전환이 끝나면
[`docs/chatgpt-supervisory-task.md`](docs/chatgpt-supervisory-task.md)에 따라 GitHub 결과와 gate만
감사·보고하는 감독자로 고정한다. 수동 대기열은 회귀 비교가 필요할 때만 사용한다.

## 주제 키워드 수정

`config/topics.yml`의 `topics.disability_rights`와 `topics.labor_care_poverty`에서 핵심어,
보조어, 법률, 정책, 단체, 인물·
별칭, 국제협약, 제외어, 조합 규칙과 점수를 수정한다. 임계값은 다음 세 값이다.

- `candidate`: 본문 2차 판별 후보가 되는 넓은 기준
- `review`: 자동 확정은 어렵지만 사람에게 넘길 기준
- `relevant`: 관련성을 자동 확정할 기준

`장애` 단독 문자열은 낮은 모호어 점수만 받는다. 서버·전산·통신·시스템 장애 등 제외 문맥이
있고 장애인 관련 강한 표현이 없으면 점수를 강제로 review 아래로 제한한다. 규칙 변경 시 오탐과
누락 사례를 모두 fixture 시험으로 추가한 뒤 전체 시험을 실행한다.

## 데이터와 보고서

- `data/articles/YYYY-MM-DD.jsonl`: 기사 본문 없는 메타데이터와 판별 결과
- `data/review/YYYY-MM-DD.jsonl`: `review` 기사만 모은 사람 검토 목록
- `data/state/source_state.json`: 출처별 마지막 실행 상태
- `reports/YYYY-MM-DD.md`: 09:00 KST 경계 일일보고
- `reports/briefings/YYYY-MM-DD.md`: 총평과 I~III 절, 선정 칼럼이 있을 때만 IV절을 덧붙인 노션 발행 원본
- `health/latest.json`: 최근 실행의 출처별 발견·신규·중복·본문 확인·API 갱신·제거·오류 집계
- `health/notion/latest.json`: 최근 노션 발행 상태(개인 페이지 URL·토큰은 기록하지 않음)
- `health/editorial/latest.json`: GPT 편집 후보·선정 수, 모델과 성공·실패 상태(본문·응답은 기록하지 않음)
- `health/editorial_queue/latest.json`: ChatGPT 임시 대기열의 후보·묶음 수와 성공·실패 상태
- `health/publish_gate/latest.json`: 기계 판정 가능한 최종 발행 허용·차단 사유
- `evidence/YYYY-MM-DD.json`: 기사별 provenance, census, gap, reverse-search, final-state 결과

브리핑 I절은 장애정책·장애인운동, II절은 노동·돌봄·빈곤, III절은 방송 장애 뉴스다. IV절
주요 칼럼은 한겨레 `세계의 창` 지제크, 미디어스 김민하, 경향신문 `고병권의 묵묵`을 주제와
관계없이 선정하고, 그 밖에는 조선·중앙·동아·한겨레·경향·오마이뉴스·프레시안의 장애 관련
칼럼만 다룬다. 선정 결과가 없으면 설명 없이 IV절 전체를 생략한다.

각 의제는 `주요 언론 보도 불릿 → 이슈 요약·보도 논조 → 추가 자료·더 알아보기`로 구성한다.
기자명이 공개 메타데이터에서 확인되면 보도 불릿에 함께 표시한다. 이전 보도는 별도 항목을 만들지
않고 더 알아보기 토글의 첫 행에 최대 3개만 표시한다. II절의 추가 자료는 `이전 보도 → 현행 제도 →
관련 연구 및 문서`만 허용하며 국제 규범과 관련 단체 입장은 넣지 않는다. 다른 절의 CRPD 조문 전문은
KDF, 일반논평은
국가인권위원회 색인표의 직접 링크를 사용하고 둘 다 `국제 규범`으로 분류한다. 기술적 설명,
선정·제외 사유, 출처 장애 정보는 브리핑에 넣지 않고 따로 연결된 보고사항 data source에
기록한다.

날짜·시간 필드는 UTC ISO 8601로 직렬화하고 화면 보고서는 KST로 변환하되 `KST` 약칭을 붙이지
않는다. 파일 분할 날짜는 기사 발행시각의 KST 날짜를 쓴다. canonical URL을 최우선 중복키로
사용하며, URL이 없을 때는
`source + article_id`, 그마저 없을 때는 `source + title + published_at` 해시를 쓴다.

## robots.txt·저작권 준수

- 모든 발견 경로와 기사 URL 요청 전에 해당 origin의 `/robots.txt`를 현재 User-Agent로 평가한다.
- robots.txt를 가져오지 못하거나 비정상 응답이면 그 origin에 대해 실패 폐쇄한다.
- 리다이렉트된 URL도 새 origin의 robots.txt를 다시 확인한다. robots.txt 자체의 cross-origin
  리다이렉트는 거부한다.
- 금지 URL은 요청하지 않고 `blocked_by_robots`로 기록한다.
- 브라우저 자동제어, User-Agent 위장, 프록시·IP 순환, CAPTCHA·로그인·유료기사 우회를 하지 않는다.
- 도메인별 요청은 직렬 처리하고 기본 2초 이상 간격을 둔다.
- 기사 본문, HTML, 대량 인용을 JSONL·fixture·로그·보고서에 저장하지 않는다.

## 장애 대응과 한계

출처 하나의 오류는 다른 출처 수집을 막지 않는다. 발견 경로 일부가 실패하면 오류를 health에
남기되 성공한 공식 경로를 처리한다. 모든 선택된 출처의 발견 경로가 실패한 경우에만 명령이
실패 코드로 끝난다. 상세 절차는 [`docs/operations.md`](docs/operations.md)를 따른다.

무료 초기 운영의 구조적 한계는 다음과 같다.

- 공식 피드·사이트맵·목록 자체가 누락하거나 늦게 게재한 기사는 즉시 발견할 수 없다.
- GitHub Actions 예약은 지연·누락될 수 있으며 저장소 용량과 실행시간 한도가 있다.
- 규칙 기반 판별은 풍자·은유·복합 맥락을 완전히 이해하지 못하므로 `review`가 필요하다.
- 사이트 구조나 robots.txt가 바뀌면 해당 출처는 안전하게 중단되며 파서 갱신 전까지 공백이 생긴다.
- 본문 접근이 금지되거나 robots.txt 확인에 실패하면 공개 메타데이터만으로 판별한다.
- 참세상은 robots.txt가 404인 동안 전체 요청을 안전 중단한다. MBC의 iMBC 웹 경로도
  `User-agent: *` 전면 금지를 준수해 요청하지 않으며, 별도로 MBCNEWS 공식 YouTube 채널의
  문서화된 Data API에서 키워드 검색과 시간순 업로드 목록을 교차확인한다.
- YouTube API 키 누락·할당량 소진·검색 또는 업로드 경로의 부분 실패는 확인 불능 또는 부분
  확인으로 기록하며 MBC 보도 0건으로 해석하지 않는다.
- 16개 매체 48시간 실측은 수천 건 규모이므로 JSONL 저장소가 장기적으로 커질 수 있다.

이 한계 때문에 **수집 실패를 기사 부재로 해석해서는 안 된다.** 장애인권 의제의 언론 비가시성을
기술적 0건과 혼동하지 않도록 보고서의 건강상태를 함께 검토해야 한다.
