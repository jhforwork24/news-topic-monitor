# 운영 점검 절차

일간 예약 루틴이 따르는 단계별 절차는 [`daily-routine.md`](daily-routine.md)에 있다. 이 문서는
그 절차 중 막혔을 때의 장애 대응을 다룬다.

## 공통 원칙

`health/latest.json`의 `success`, `discovery_status`, `errors`, `structure_warnings`,
`discovered`, `new`, `duplicates`, `bodies_checked`, `bodies_blocked`, `refreshed`, `removed`를
먼저 확인한다. 기사 0건을 곧바로 보도 부재로
해석하지 않는다. 장애인권 의제가 실제로 배제된 것인지, 수집 기반이 끊긴 것인지를 반드시
구분한다.

## 출처 구조 변경

1. 실패한 공식 RSS·사이트맵·목록 URL과 robots 판정 상태를 확인한다.
2. `MONITOR_CONTACT`가 설정된 로컬 smoke 환경에서 해당 출처만 실행한다.
3. 응답이 성공하면 비밀값·기사 본문을 출력하지 않고 태그 이름, JSON key, 선택자 존재 여부만
   점검한다.
4. 언론사의 공식 공개 경로 안에서 대체 경로를 확인한다. 검색엔진 캐시, 프록시, 모바일 우회,
   로그인·유료 경로는 사용하지 않는다.
5. 파서를 고치면서 필요한 최소 구조만 합성 fixture에 반영한다. 실제 기사 본문은 복제하지 않는다.
6. 출처 fixture 시험, 전체 오프라인 시험, ruff를 실행한 뒤 live smoke를 다시 실행한다.
7. 확인 날짜와 결과를 README 또는 변경 기록에 남긴다. 확인하지 못한 선택자는 작동한다고
   보고하지 않는다.

본문 선택자만 깨졌다면 URL·제목·날짜 등 발견 메타데이터는 보존하고 `parse_error`와
`verification_status=extraction_failed`를 기록한다. 다른 출처 수집을 중단하지 않는다.

## robots.txt 변경 또는 확인 실패

1. 해당 origin의 `/robots.txt` HTTP 상태, 최종 URL, 응답시각을 확인한다.
2. 현재 User-Agent `KCILNewsMonitor/0.1 (+연락처)`에 적용되는 가장 구체적인 규칙을 확인한다.
3. 금지로 바뀌었으면 코드를 우회 수정하지 않는다. 요청하지 않은 사실과 영향을 health·보고서에
   남긴다.
4. timeout, 403, 429, 5xx, 파싱 불가이면 일시 장애일 수 있으나 허용으로 추정하지 않는다.
   다음 예약 실행의 재시도를 기다린다. 다만 **편집 대기열을 만들 때의 응답 시간초과**(robots.txt
   확인 포함)만은 대기열 생성 단계가 3분 간격으로 최대 2회 새 클라이언트로 같은 확인을 다시 한다
   (아래 "출처 시간초과로 후보가 빠짐" 참조). 403·429·5xx·파싱 불가는 재시도하지 않는다.
   404/410(robots.txt 없음)도 기본은 fail-closed다. 사용자가 승인해
   `config/source-registry.yaml`에 `robots_absent_policy: allow_if_absent`를 둔 출처(현재
   없음; 참세상은 2026-10-07 robots.txt 게시로 철회)에 한해 RFC 9309에 따라 "robots.txt 없음 = 제한 없음"으로 읽고, health
   `robots_absent_origins`와 대기열 매니페스트 `출처 점검`에 그 사실을 남긴다. 그런 출처가
   robots.txt를 새로 게시하면 그 규칙을 그대로 따른다.
5. robots가 가리키는 새 공식 sitemap이 있다면 그 경로 자체의 허용 여부와 형식을 별도로 검증한
   뒤 어댑터 변경을 검토한다.

## GitHub Actions 실패

1. 세 워크플로에 `MONITOR_CONTACT` Repository variable이 노출되는지 확인한다. 값 자체를
   로그에 출력하지 않는다.
2. Actions의 job conclusion과 `health/latest.json`을 비교한다. 모든 출처 실패인지, 테스트·설치·
   push 단계 실패인지 분리한다.
3. 예약 누락이면 Collect를 수동으로 최근 6시간보다 넓게 실행하고 Daily backfill을 48시간으로
   실행한다.
4. 의존성 설치 오류는 `pyproject.toml`과 Python 3.12 여부를 확인한다. 무심코 major version 상한을
   제거하지 않는다.
5. 전체 출처 실패는 실패로 유지한다. 성공으로 가장하기 위해 `continue-on-error`를 파이프라인
   전체에 적용하지 않는다.

## 연결형 Claude 브리지 실패

대기열·초안·감사는 `NOTION_QUEUE_DATA_SOURCE_ID`가 가리키는 `대기열 초안 감사 등` data
source에 있다. `NOTION_REPORTS_DATA_SOURCE_ID`(브리핑 보고사항)는 별도이며 발행 성공·실패
등 특이 보고사항 전용이므로 여기서 대기열·초안·감사를 찾지 않는다.

1. 순서대로 오늘의 `health/editorial_queue/latest.json`, private Notion 대기열 매니페스트와 모든
   묶음, `Claude 편집 초안 · YYYY-MM-DD`, `Claude 독립 감사 · YYYY-MM-DD`를 확인한다.
2. 대기열 health의 `queue_id`와 세 종류 페이지의 `queue_id`가 같아야 한다. 후보 묶음 번호가
   1부터 `part_count`까지 연속이고 후보 합계가 `candidate_count`와 같은지도 확인한다. 값이 다르면
   과거 초안·감사를 오늘 입력으로 재사용하지 않는다.
3. 초안과 감사 페이지는 같은 exact title의 활성 페이지가 각각 정확히 1개이고, 기계 판독용 JSON
   code block도 각각 정확히 1개여야 한다. 중복 페이지를 임의로 선택하지 않는다.
4. `draft.submitted_at >= manifest.generated_at`, `audit.submitted_at >= draft.submitted_at`, 감사의
   `draft_id`가 초안과 같은지 확인한다. 시각·ID가 어긋나면 editor부터 새 queue_id에 맞춰 다시
   실행하고 그 뒤 auditor를 실행한다.
5. `미확인 candidate_id`, 중복 선정, 비어 있는 이슈, 한 이슈 5개 초과, 한 섹션 10개 초과,
   확인 불가능 본문 근거는 기계 검증 실패다. 초안 JSON을 손으로 우회 수정하지 않고 편집 작업의
   근거 선택을 바로잡는다.
6. 연결형 Claude가 실패해도 `editorial-publish.yml` 유료 API 경로가 자동 시작되지는 않는다.
   원인·대체경로·결과·다음 조치를 남기고, 사용자가 비용을 명시적으로 승인한 경우에만 수동
   fallback을 검토한다.
7. finalizer가 "초기 전수 수집 health 스냅샷이 없음" 또는 "…일치하지 않음"으로 실패하면
   `health/editorial_queue/initial_health/YYYY-MM-DD.json`이 있는지, 그 안의 queue_id가 오늘
   매니페스트와 같은지 확인한다. 이 스냅샷은 대기열 생성 시 한 번만 기록되며 이후 정기
   collect·backfill 실행이 `health/latest.json`을 덮어써도 영향받지 않는다. 스냅샷 자체가
   없으면 대기열을 다시 생성해야 하며, `health/latest.json`만 다시 채워 넣는 방식으로 우회하지
   않는다.

## 대기열이 제때 없을 때

`editorial-queue.yml`의 예약은 05:10 KST지만 GitHub 예약 트리거 자체가 늦게 발동한다.
2026-09 하순 이후에는 거의 매일 07:55~08:50 KST였고 드물게 8시간까지 늦었다. 기다리지 말고 workflow_dispatch로 직접 실행한다
(`date=YYYY-MM-DD`, `dry_run=false`). 실행은 보통 10~15분 걸린다.

- 이미 오늘 매니페스트가 있으면 그 실행은 `status=already_built`로 아무것도 하지 않고 정상
  종료한다(health의 status도 같은 값). 실패가 아니므로 그대로 두면 된다. 뒤늦게 발동한 예약
  실행이 진행 중인 작업을 덮어쓰지 못하게 막는 장치다.
- `force` 입력은 이 보호를 끄고 대기열을 새로 만든다. 새 queue_id가 생기므로 그 시점에 이미
  제출된 초안은 finalize에서 queue_id 불일치로 거부된다. 초안을 버릴 의도가 분명할 때만 쓴다.

## 큐 run이 preflight 단계만 실패

`editorial-queue.yml`의 마지막 단계 `Start private model-free briefing preflight after a ready queue`는
대기열이 준비되면 비공개 시험 저장소의 `preflight.yml`을 dispatch한다. 시크릿
`BRIEFING_TRIAL_DISPATCH_TOKEN`이 없으면 경고만 내고 통과하지만, 값이 있는데 권한이 모자라면 run이
`failure`로 끝난다. 대기열 데이터는 이 단계 전에 이미 만들어져 커밋되므로 대기열·초안·감사·발행에는
영향이 없고, 일간 루틴은 이 단계만 실패한 run에서 중단하지 않는다(`docs/daily-routine.md` 1단계 3항).
오류 코드별 원인은 다음과 같다.

- `403 Resource not accessible by personal access token`: 토큰이 대상 저장소에 접근하지 못하거나
  `Actions` 쓰기 권한이 없다. fine-grained 토큰의 Repository access에 시험 저장소가 선택돼 있고
  Repository permissions의 **Actions: Read and write**가 있어야 한다(`Secrets` 권한과는 다르다).
- `404`: 토큰이 저장소를 볼 수 없거나 시험 저장소 `main`에 `preflight.yml`이 없다.
- `401`: 토큰이 틀렸거나 만료됐다. 시크릿 이름은 정확히 `BRIEFING_TRIAL_DISPATCH_TOKEN`이어야 한다.
- `422`: 시험 저장소의 `preflight.yml`이 `workflow_dispatch`나 `date` 입력을 받지 않는다.

시크릿에는 필요한 권한(대상 저장소 하나의 Actions 쓰기)만 가진 전용 토큰을 쓴다. 공개 저장소의
시크릿이므로 다른 저장소의 시크릿 쓰기 같은 권한이 있는 토큰을 두지 않는다. 확인용으로 대기열을
미리 만들지 않는다(그날 대기열이 오래된 수집으로 고정된다). 다음 정규 큐 run의 결과로 확인한다.

## 발행을 건너뛰는 날 (공휴일 등)

화~토 가운데 쉬기로 한 날은 사용자가 정하며 `config/publication-calendar.yaml`의
`skipped_publication_dates`에 `date`와 `reason`을 더하는 PR로 남긴다. 그 날은 예약 루틴이
`docs/daily-routine.md` 0단계에서 아무것도 시작하지 않고 보고하며, finalize 예약 안전망도 조용히
종료한다(수동 dispatch는 명시적 지시이므로 진행한다). 대기열 예약은 그 날에도 대기열을 만들지만
무해하다. 다음 실제 발행일의 조사 구간은 직전 실제 발행일 05:00 KST부터로 자동으로 넓어진다 —
후보 상한(화요일 360건)은 그대로라 검토 분량은 거의 늘지 않는다. 날짜는 늦어도 쉬는 날 07:35 전에
병합한다. 파일이 없거나 형식이 틀리면 조사 구간 계산이 설정 오류로 멈춘다(빈 목록으로 취급하지 않는다).

## 고정 칼럼 누락

발행 게이트가 "대기열에 있는 고정 칼럼이 초안에 선정도 제외도 되지 않음"으로 막으면, 그날
한겨레 `세계의 창`(지제크)·미디어스 김민하·경향 `고병권의 묵묵` 중 하나가 실제로 게재됐는데
초안이 처리하지 않은 것이다. 해당 candidate_id를 `opinion` 이슈에 넣거나, 싣지 않는 이유를
`plan.exclusions`에 남긴 뒤 다시 발행한다. 게이트를 우회하지 않는다.

이 세 칼럼은 필자명이 매체마다 다른 필드에 있다(경향은 제목, 한겨레·미디어스는 요약의 바이라인
줄). 그래서 수집 단계에서 제목만으로 irrelevant 판정돼도 본문을 확인하고, 대기열 상한과 무관하게
후보로 고정 배정하며, 후보 줄에 `III절 고정 칼럼` 힌트를 붙인다. 이 힌트가 없으면 그날 그
칼럼이 게재되지 않았다는 뜻이지 후보가 걸러졌다는 뜻이 아니다.

## 예약 수집 공백과 수집 창 이어 붙이기

`collect.yml`은 3시간 간격(하루 8슬롯)으로 예약되어 있지만 GitHub는 예약 실행의 일부 슬롯을 건너뛰거나
1~3시간 늦게 시작한다. 2026-10-17~26 실측에서 실행은 하루 6~7회였고 간격이 4.3~8.6시간까지 벌어졌다.
수집 창은 `--since-hours 6`이므로 간격이 6시간을 넘으면 그 사이에 게재된 기사는 목록에서 발견되어도
창 밖이라 저장되지 않았다.

`collect` 예약은 출처별로 직전에 **오류 없이 끝난** 수집(`data/state/source_state.json`의
`last_complete_at`)에서 1시간 겹쳐 이어지도록 창 시작을 앞당긴다(최대 48시간). 앞당긴 출처는
`health/latest.json`의 `extended_window_start`에 실제 시작 시각이 남는다. 일부 경로가 실패한 회차는
`last_success_at`만 갱신하므로 그 경로의 몫은 다음 회차가 다시 읽는다. `--start`를 직접 준 수동 실행,
backfill, 편집 대기열·finalize는 이 보정을 쓰지 않는다. 같은 출처에서 `extended_window_start`가 계속
나오면 예약 실행이 자주 빠지고 있다는 뜻이므로 Actions 실행 이력을 확인한다.

## 출처별 병렬 수집 (`COLLECT_WORKERS`)

대기열 run은 본문 확인(출처당 최대 24건×일수, 건당 최소 2초 간격)이 시간의 대부분이라 14개 출처를
차례로 돌면 화요일(72시간 창) 약 28분이 걸렸다(2026-10-27: 수집 단계 27분, 조선일보 6.2분). 출처는
도메인이 서로 달라 동시에 돌려도 도메인별 간격·동시 1건 제한이 지켜지므로, `Collector`가 출처별
수집을 스레드로 동시에 실행한다(기본 4, 환경변수 `COLLECT_WORKERS`, 1~8; 1이면 순차).

- 도메인마다 잠금이 있어 같은 도메인의 요청은 동시에 나가지 않고 `REQUEST_INTERVAL_SECONDS` 간격을
  지킨다. robots.txt 판정은 origin마다 한 번만 가져온다.
- 저장소·증거 저장소 쓰기는 `Collector`의 잠금으로 직렬화한다. 출처 한 곳의 실패는 다른 출처에
  영향을 주지 않는다(예전과 같은 격리).
- 수집 결과(health 출처 순서, 저장 기사, 증거 후보)는 순차 실행과 같다. 이상이 의심되면
  `COLLECT_WORKERS=1`로 되돌려 비교한다.
- 첫 실측은 다음 대기열 run의 health `run_started_at`~`run_finished_at`과 출처별 소요로 확인한다.

## 포착률 점검 (조선일보 매일, 전 매체 주 1회)

수집기는 목록에 100건만 있는 출처(조선일보 RSS·sitemap은 낮 시간대 1~2시간 분량), GitHub 예약의
누락·지연, 수집 창보다 긴 실행 간격 때문에 오류 없이 기사를 놓칠 수 있다. 이를 눈으로 찾지 않도록
`coverage-check`(모델 없음, 기사 본문 수집·저장 없음)가 각 어댑터의 공식 발견 경로를 점검 구간 전체에
다시 적용해 목록에 보이는 기사 가운데 저장분에 있는 비율을 센다. robots.txt 확인·2초 간격·연락처
User-Agent는 수집기와 같다.

- 실행: `coverage-check.yml`. **조선일보만 매일 06:47 KST**(점검 48시간), **전 매체는 일요일
  23:07 KST**(점검 168시간). 수동 실행은 `scope`(chosun·all)를 고른다. 수집·대기열·finalize와 같은
  동시성 그룹을 쓴다.
- 결과: `health/coverage/latest.json`에 범위(`chosun`·`all`)별로 마지막 결과를 남긴다. 출처마다
  `status`(pass·fail·error·not_judged), `rate`, `listed`, `captured`, 판정 구간(`judged_from`·
  `judged_until`), 누락 URL·제목·발행 시각(최대 30건), 실패한 목록 경로(`errors`)가 있다. 기사 본문은
  없다.
- 목표: 장애·노동 전문지(비마이너·에이블뉴스·더인디고·매일노동뉴스·참세상) **100%**, 그 밖의 매체
  **99% 이상**. 미달(`fail`)이거나 모든 목록 경로가 실패(`error`)하면 실행이 실패로 표시된다.
- 판정 범위: 목록은 자기가 아직 닿는 기간만 보증한다(예: 동아 약 25시간, 한겨레 약 35시간, 에이블뉴스
  약 4일). 그래서 점검 창 안에서 목록이 닿는 가장 이른 시각부터, 마지막 성공 수집 1시간 전(목록 반영
  지연 여유)·현재 3시간 전 가운데 이른 시각까지를 판정한다. 이 구간 밖의 기사는 누락으로 세지 않는다.
  목록에 발행 시각이 하나도 없는 출처는 `not_judged`다. 조선일보의 스포츠·연예·일본어판·영문판은 수집
  대상이 아니므로 세지 않는다(`excluded_from_coverage`).
- 한계: 이 점검은 발견 경로를 다시 적용한 결과와 저장분을 비교하므로, 어댑터가 쓰는 목록 자체에 없는
  기사는 찾지 못한다. 매체 고유의 별도 전체 목록(동아 전체기사 목록, 오마이뉴스 전체기사 목록, 프레시안
  월별 sitemap, 번호 빈틈 확인)은 구조를 공식 사이트로 확인한 뒤 어댑터 경로로 추가한다.

`fail`이 나오면 `missing`의 URL과 `judged_from`부터 보고, 같은 시각 근처의 collect 실행이 빠졌는지
(위 "예약 수집 공백과 수집 창 이어 붙이기")와 해당 출처의 `errors`를 먼저 확인한다. 누락을 기사
부재로 해석하지 않는다.

## 출처 시간초과로 후보가 빠짐

대기열 매니페스트 `출처 점검`에 "응답 시간초과가 재시도 2회 뒤에도 이어져 이 출처의 당일 기사가
후보에서 빠짐"이 있으면, 그 출처의 당일 기사는 편집·감사를 거치지 않는다. 기사 부재가 아니다.
2026-10-01 프레시안 robots.txt 시간초과로 장애 관련 기사 1건(최혁진 의원 중증장애인생산품
우선구매 전수조사)이 발행본에서 빠졌고 사후에 보완했다. 수집 기록상 9월 1일 이후 프레시안은
약 10%가 같은 시간초과로 실패했고, 같은 날 몇 시간 안에 다시 성공했다(robots 차단이 아니다).

- 대기열 생성: 시간초과로 실패한 출처만 `QUEUE_TIMEOUT_RETRY_DELAY_SECONDS`(기본 180초) 간격으로
  `QUEUE_TIMEOUT_RETRY_ROUNDS`(기본 2회)까지 새 HTTP 클라이언트로 다시 수집한다. 0이면 끈다.
  복구되면 후보에 포함되고 `출처 점검`에 "재시도 N회차에 복구" 줄이 남는다. 403·429·5xx·파싱 불가
  등 시간초과가 아닌 실패는 재시도하지 않고 기존대로 안전하게 중단한다(robots 우회 아님).
- 발행(finalize): 대기열 생성 때 시간초과로 실패한 출처를 한 번 더 수집해, 대기열에 없던 관련
  기사(장애 또는 노동·돌봄·빈곤·소수자 기준 relevant)가 있으면 발행 게이트가 **warning** 보고사항으로
  남긴다(발행은 막지 않는다). 이 기사는 편집·감사를 거치지 않았으므로, 보고사항에 제목이 나오면
  원문을 확인해 필요하면 사후 보완하고 브리핑 정정 이력에 기록한다.
- 예약 루틴이 대기열을 받았을 때 `출처 점검`에 이 줄이 있으면 해당 출처 기사를 직접 찾아 후보와
  대조하지 말고(대기열 밖 기사는 선정 불가), 발행 후 게이트 보고사항을 확인해 사용자에게 알린다.

## 비마이너 오피니언 미선정

발행 게이트가 "대기열에 있는 비마이너 오피니언이 초안 opinion 섹션에 선정되지 않음"으로 막으면,
비마이너 오피니언 섹션 기사(기사 페이지 `article:section`=오피니언)를 초안이 `opinion`에 넣지
않은 것이다. 비마이너 한 곳에 한해 오피니언 전부를 III절에 싣는 조건이라 고정 칼럼과 달리
`plan.exclusions`로 면책되지 않는다. 해당 candidate_id를 `opinion` 이슈에 넣은 뒤 다시 발행한다.

비마이너는 sitemap 발견 단계에 섹션이 없어 수집 파이프라인이 비마이너 기사의 본문 페이지를
robots.txt 허용 범위에서 열어 섹션 라벨을 읽는다. 본문 확인이 실패(robots 차단·HTTP 오류·구조 변경)한
기사는 섹션을 알 수 없어 후보 힌트가 붙지 않으므로, 힌트가 없다는 사실을 오피니언이 없다는
근거로 해석하지 않고 health의 본문 확인 실패를 함께 확인한다.

## 인사 소식 감시 대상 미처리

발행 게이트가 "대기열에 있는 인사 소식 감시 대상(기관장급)이 초안에 선정도 제외도 되지
않음"으로 막으면, 한국장애인개발원·한국장애인고용공단·국민연금공단·보건복지부·고용노동부의
원장·이사장·장관·차관·국장급 인사 기사가 있는데 초안이 처리하지 않은 것이다. 해당
candidate_id를 관련 섹션(`disability` 또는 `labor`) 이슈에 넣거나, 싣지 않는 이유를
`plan.exclusions`에 남긴 뒤 다시 발행한다. 게이트를 우회하지 않는다.

같은 기관의 본부장·센터장·실국장·과장급 인사는 `degraded_warnings`에만 남고 발행을 막지 않는다
— 과장급 전보처럼 빈번한 인사로 발행 전체가 막히는 것을 피하기 위해서다. 발행은 계속 진행하되,
다음 편집에서 선정 또는 제외 사유를 남길지 검토한다.

기관·직위명만으로는 그 기관을 다루는 거의 모든 정책 기사에 걸리므로, 실제 인사 이동을 가리키는
이벤트 용어(임명·내정·취임·선임·지명·신임·발탁·이임·퇴임·사임·사퇴·물러나·후보자·교체)가 함께
나올 때만 매칭한다(`src/news_topic_monitor/sources.py`의 `monitored_personnel_match`). 그래서
수집 단계에서 제목만으로 irrelevant 판정돼도 본문을 확인하고, 대기열 상한과 무관하게 후보로 고정
배정하며, 후보 줄에 `인사 소식 감시 대상` 힌트를 붙인다. 이 힌트가 없으면 그날 그 인사 소식이
없었다는 뜻이지 후보가 걸러졌다는 뜻이 아니다.

## 핵심 일간지 본문 재확인 (층 A·B·C)

조선·중앙·동아·한겨레·경향 기사는 제목·요약에 장애 용어가 없어 title-only 판정이 irrelevant로
끝나도 본문에서 장애인권 의제를 다룰 수 있다(2026-10-20 한겨레 "AI가 만든 세계에서 '소수자'는
지워진다" 단독 보도). 수집 단계(`pipeline.py`)는 아래 단서가 있으면 분류와 무관하게 본문을 열어
다시 분류한다(`src/news_topic_monitor/sources.py`의 `body_recheck_match`).

- 층 A: 제목에 `단독`이 있고 제목·요약에 소수자·사회적 약자·취약계층·사각지대·인권·혐오·배제가 있음.
- 층 B: 단독 여부와 무관하게 정신병원·정신의료기관·정신질환·강제입원·교통약자·이동권·돌봄·요양 등
  장애 의제와 맞닿은 정책 용어가 제목·요약·섹션에 있음. `격리`·`학대`처럼 일반 사건 기사에 흔한
  단어는 단독 단서로 쓰지 않는다(`격리·강박`만 사용).
- 층 C: `config/topics.yml`에 정신병원·정신의료기관·강제입원·비자의입원 supporting term과
  `격리`+`강박` 조합을 추가해 제목만으로도 후보(candidate)에 도달하게 한다. 이 층은 출처와 무관하다.

본문 재분류에서도 장애 관련성이 없으면 종전처럼 irrelevant로 남는다. 이 규칙은 후보 확대일 뿐
선정·발행 판단을 바꾸지 않으며, 다른 출처(오마이뉴스·프레시안·시사IN)로의 확대는 운영 결과를
본 뒤 결정한다. 규칙을 바꾸면 `tests/test_sources.py`와 `tests/test_pipeline.py`를 함께 고친다.

## 초안·감사 페이지의 날짜 속성 누락

finalize가 "활성 Notion 페이지가 정확히 1개여야 함: Claude 편집 초안 · YYYY-MM-DD (found=0)"
또는 "... Claude 독립 감사 · YYYY-MM-DD (found=0)"로 실패하면서, 해당 페이지가 Notion에 실제로
존재하고 제목도 정확하며 본문 JSON의 `report_date`도 맞다면 — 원인은 페이지 자체의 구조화된
`날짜` 속성이 비어 있는 것이다(2026-09-22 실제 발생 사례: 감사 서브에이전트가 JSON 본문의
`report_date`는 정확히 썼지만 Notion 페이지 속성 패널의 `날짜`를 설정하지 않았다). finalizer는
제목과 `날짜` 속성을 함께 필터링해 페이지를 찾으므로(`notion_publish.py`의 `_query_exact`), 본문에
적힌 날짜 문자열은 이 조회와 무관하다.

`notion-fetch`로 해당 페이지의 `properties`를 확인해 `date:날짜:start`가 report_date로 채워져
있는지 본다. 비어 있으면 `notion-update-page`(command=`update_properties`)로
`{"date:날짜:start": "YYYY-MM-DD", "date:날짜:is_datetime": 0}`를 설정하고, finalize를 재트리거한다
— 초안·감사 내용 자체를 다시 쓸 필요는 없다. `docs/claude-editorial-instructions.md`·
`docs/claude-auditor-task.md`의 제출 계약에 날짜 속성을 재확인하라는 지침이 있으니, 페이지 생성
직후 반드시 재조회해 이 속성이 비어 있지 않은지 눈으로 확인한다.

## Publish gate 차단

1. `health/publish_gate/latest.json`에서 `allowed`, `fatal_errors`, `degraded_warnings`,
   `reporting_items`를 확인한다. `allowed=false`인 실행은 Notion 최종 브리핑이 없어야 정상이다.
2. 장애언론 census는 비마이너·에이블뉴스·더인디고 3곳 모두 `complete`여야 한다. rolling 목록이
   100건 상한에 닿았으면 가장 오래된 발견시각이 조사 시작시각 이전인지 확인한다. 경계에 닿지
   않았으면 검색 API 결과와 무관하게 census를 COMPLETE로 수동 변경하지 않는다.
3. 지정매체 역검색은 이슈마다 정확히 10개 상태가 있어야 한다. Naver API Hub 미설정·일시 실패는
   `degraded`로 분류할 수 있지만, 해당 결과를 원문 본문 확인으로 승격하지 않는다.
4. `validator_fatal_errors`가 1 이상이면 evidence와 초안을 대조해 조사기간 밖 선행보도 오인,
   행위자·수치·현재상태 오류, 잘못된 이슈 통합을 먼저 해결한다.
5. `final_state`에 `changed_after_draft=true`가 있으면 새 원문을 반영해 재편집·재감사하기 전까지
   발행하지 않는다. 발견 URL을 지우거나 상태를 우회하지 않는다.
6. `health/editorial/latest.json`의 `phase_durations_seconds`에서 `initial_collection`,
   `gpt_edit_audit`, `final_state_recrawl`, `gap_reverse_search`, `total`을 확인한다. 초안·감사 뒤
   시작된 `final_state_recrawl` health가 없으면 COMPLETE 상태를 신뢰하지 않는다.
7. 모든 실패는 `원인·대체경로·결과·다음조치` 네 항목으로 보고사항에 남긴다. 재실행 전
   `evidence/YYYY-MM-DD.json`과 새 health가 이전 실행을 조용히 덮어써 원인을 잃지 않는지 확인한다.

## API preflight 실패

1. `health/api_preflight/latest.json`에서 실행 route와 OpenAI·Naver API Hub 상태를 분리해 확인한다.
   무료 production finalizer에서 OpenAI는 `not_required`, route는
   `connected_claude_automation`이어야 한다.
2. 수동 유료 fallback의 OpenAI HTTP 429 `insufficient_quota`는 API billing·project budget·credits를 확인하고,
   `rate_limit_exceeded`는 프로젝트 rate limit과 후보 묶음 크기를 확인한다. 두 상태를 API key
   미등록이나 기사 부재로 바꾸지 않는다.
3. Naver preflight가 degraded이면 API HUB client ID·secret의 등록 위치, migration endpoint와
   할당량을 확인한다. 검색 결과를 공식 원문 확인으로 승격하지 않는다.
4. health와 로그에는 공급자 응답 message, 요청 본문, key를 복제하지 않는다.

## 데이터 push 충돌

워크플로는 동일 concurrency 그룹으로 상호 직렬화되고 push 실패 시 최대 3회 `pull --rebase`한다.

1. rebase 충돌이 나면 자동으로 한쪽 JSONL을 덮어쓰지 않는다.
2. 양쪽 파일을 JSONL로 파싱해 canonical URL key 기준으로 합치고, `first_seen_at`은 이른 값,
   `last_seen_at`은 늦은 값을 유지한다.
3. 같은 key의 판별·메타데이터가 다르면 더 최근 `last_seen_at` 레코드를 기본으로 하되 본문이
   저장되지 않았는지 확인한다.
4. 날짜별 파일, review 파일, state와 health를 재생성한 뒤 멱등성 시험을 실행한다.
5. 강제 push는 사용하지 않는다.

## Notion 발행 실패

1. `NOTION_PUBLISH_ENABLED`, `NOTION_DATA_SOURCE_ID`, `NOTION_TOKEN`의 등록 위치를 확인한다.
   토큰 값은 로그에 출력하지 않는다.
2. 내부 통합이 대상 브리핑 data source, `대기열 초안 감사 등` data source
   (`NOTION_QUEUE_DATA_SOURCE_ID`), 보고사항 data source(`NOTION_REPORTS_DATA_SOURCE_ID`)
   모두에 연결되어 있는지 확인한다.
3. `health/notion/latest.json`의 `configuration_error`, `failed`, `created` 상태와 `version`을 본다.
4. 같은 날짜에 브리핑 제목을 포함한 페이지가 이미 있으면 상태가 `already_published`이고 새 페이지가
   없어야 정상이다. 이는 GitHub 재시도와 전환기 Claude 작업이 동시에 같은 날짜를 발행하는 것을
   막는 날짜 단위 멱등성이다.
5. API 버전·속성명이 바뀌면 공식 Notion API 문서와 실제 data source schema를 먼저 확인하고
   MockTransport 시험을 갱신한다.
6. 발행 실패는 수집 데이터 실패가 아니다. `reports/briefings/` Markdown을 보존한 채 수동 재실행한다.
   이미 같은 날짜 브리핑이 있으면 재실행은 그 페이지를 덮어쓰거나 새 버전을 만들지 않는다.
7. `Notion-Version: 2026-03-11`에서 임시 큐 페이지를 정리할 때는 `archived`가 아니라
   `in_trash=true`를 사용한다. 400 validation error가 보이면 배포 코드와 MockTransport 시험이 이
   필드를 사용하는지 확인한다.

## 오탐·누락 조정

### 오탐

1. 해당 기사에서 일치어, 제외어, 필드별 문맥과 점수를 확인한다.
2. `장애` 단독, 서버·전산·통신·운행 문맥이면 `excluded_terms` 또는 조합 조건을 보강한다.
3. 장애인 관련 실제 기사까지 함께 배제하지 않도록 강한 인간·권리 문맥 동시 사례를 회귀시험에
   추가한다.
4. 임계값 전체를 크게 올리는 방식은 소수자 의제 누락을 키우므로 마지막 수단으로 삼는다.

### 누락

1. 먼저 source health가 성공인지 확인한다. 수집 실패라면 주제 규칙 문제가 아니다.
2. 공식 발견 경로에 URL이 있었는지, 기간 경계와 발행시각이 올바른지 확인한다.
3. 장애 유형·정책·단체·법률의 새로운 표현을 적절한 그룹에 추가한다.
4. 제목이 완곡하거나 낙인적 표현을 쓰는 경우 인권 관점의 조합 규칙을 보강하고 review 임계값을
   검토한다.
5. 실제 기사 문장을 fixture로 복제하지 말고 맥락을 보존한 짧은 합성 문장으로 시험한다.

규칙 변경은 `relevant`, `review`, `irrelevant` 세 경계와 비정책적 장애 용례를 모두 통과해야 한다.

## 저장량과 보존

기사 메타데이터와 일일보고는 보존한다. GitHub Actions 자체 로그 보존기간은 저장소 설정에서
필요 최소한으로 줄일 수 있지만, 코드가 `data/articles`, `data/review`, `reports`, `health`를
일괄 삭제해서는 안 된다. 저장소 용량이 커지면 D1 이전 절차를 따르되 유료 서비스가 필요하면
먼저 사용자 승인을 받는다.

**`last_seen_at` 갱신 제한 (2026-10-08).** 수집마다 목록에 남은 모든 기사를 다시 보고 `last_seen_at`만
바꿔 쓰는 바람에, 데이터 커밋 한 번에 바뀐 행의 85~95%가 이 필드만 다른 행이었고 날짜별 JSONL
파일이 통째로 다시 쓰였다(저장소 `.git`이 277MB). 일반 수집(`collect`·`backfill`)은 이제 다른
필드가 그대로인 기사의 `last_seen_at`을 24시간에 한 번만 갱신한다(`LAST_SEEN_REFRESH_HOURS`). 판별·
요약·`discovery_route` 같은 실제 변경은 즉시 쓰고, 처음 보는 기사는 그대로 추가한다. 대기열·
finalize 같은 증거를 만드는 실행은 매번 갱신해 증거 매니페스트의 `last_checked_at`이 그 실행 시각을
따른다. 기존 기록을 삭제하거나 덮어쓰지 않고 쓰기 횟수만 줄이며, 이미 쌓인 이력은 그대로 둔다.
따라서 변경 없는 기사의 `last_seen_at`은 최대 24시간 늦을 수 있고, 이 값은 "목록에서 사라졌는지"를
판정하는 데 쓰지 않는다. 용량이 계속 문제면 24시간 값을 늘리거나 `fetch-depth` 축소를 검토한다
(`scripts/commit_outputs.sh`의 `git pull --rebase`가 얕은 복제에서 동작하는지 먼저 확인한다).

## 노션 쓰기 도구가 승인 대기에 걸림

연결형 Claude 서브에이전트(편집·감사)의 `notion-create-pages`·`notion-update-page`가 응답하지 않고
멈추는 장애다. 2026-09-29부터 간헐적으로 나타났고(그 전 9/19~9/25에는 쓰기가 1초 안에 끝남), 10-03에는
2시간 21분, 10-06에는 최대 65분을 기다렸다. 읽기 도구(`notion-fetch`·`search`·`query`)는 한 번도
막히지 않았다. 대기 중 컨테이너가 재시작되면 서브에이전트가 소멸하고 초안·감사 페이지가 만들어지지 않는다.

- **진단:** `python3 scripts/notion_write_latency.py --since <UTC 시각>`로 세션 기록에서 쓰기 호출의
  대기 시간을 확인한다. 응답 없이 사라진 호출은 `UNANSWERED`로 표시된다. 같은 스크립트가 Notion 쓰기가
  아닌 서브에이전트 도구 호출이 120초를 넘긴 경우도 `STALL`로 보고한다(2026-10-17 편집 서브에이전트의
  `rm -f work/*` Bash 호출이 3,619초 걸린 사례). 서브에이전트 규칙은 삭제 명령을 쓰지 않게 한다.
- **원인 후보(가능성 순):** (1) 노션 서버 업데이트나 커넥터 재인증으로 도구별 권한이 기본값(쓰기는 승인
  필요)으로 돌아감 — 10-06 07:35에 새 도구(`notion-restore-pages`)가 추가된 것과 시점이 맞음,
  (2) 노션 커넥터가 서버 이름과 UUID로 두 벌 보여 설정한 쪽과 세션이 쓰는 쪽이 다름(패치노트 5.21의
  MCP 접두사 혼용 참조), (3) 여러 기기에서 설정이 서로 덮어씀, (4) 조직 관리자 상한. 저장소
  `.claude/settings.json`의 `mcp__Notion__*` 허용은 이 승인을 막지 못했다.
- **"항상 허용"으로 둔 뒤에도 재발한 사례(2026-10-13):** 같은 날 아침 편집·감사의 `notion-create-pages`는 즉시
  (0초) 응답했고, 직후 감사의 `notion-update-page`가 응답 없이 소멸(`UNANSWERED`)했으며, 저녁에는 `update-page`
  (`replace_content` 포함)가 847초·580초 대기했다. `create-pages`는 통과하고 `update-page`만 걸렸고, 같은
  `update-page`가 10-09에는 즉시 통과했다. 설정이 몇 초 사이에 바뀌었다기보다 10-09~10-13 사이 도구 분류가
  바뀐 것(서버 업데이트로 `update-page`의 위험도 표시가 바뀌어 저장한 허용이 풀림)으로 본다. 설정을 확인할 때는
  (1) 커넥터 목록에 Notion이 한 벌인지 두 벌(이름·UUID)인지, (2) `update-page`가 허용으로 되어 있는지(쓰기·삭제
  도구 묶음 포함), (3) 조직 관리자 상한이 있는지, (4) 풀린 시각을 본다. 세션 권한 모드(Accept edits·Plan·Auto)는
  커넥터 도구 권한과 별개다. 이 승인 대기는 연결된 Claude 세션의 Notion MCP 쓰기에만 걸리고, GitHub Actions의
  Notion API 토큰(finalize 발행)은 영향이 없다.
- **해소:** 사용자가 claude.ai → Customize → Connectors → Notion의 도구 권한에서
  `notion-create-pages`·`notion-update-page`를 항상 허용으로 둔다(이 세션은 바꿀 수 없다). 다시 풀리면
  풀린 날짜·시각을 기록해 위 후보를 좁힌다.
- **임시 대응:** `docs/daily-routine.md`의 "반환 모드"로 서브에이전트가 Notion에 쓰지 않게 한다.

## Claude 토큰 사용량 측정

연결된 Claude의 편집·감사 서브에이전트가 일간 루틴 토큰의 대부분을 쓴다. 세션 기록만 읽어(내용은 출력하지
않는다) 서브에이전트별 사용량을 합산한다.

```
python3 scripts/subagent_token_report.py --since {UTC ISO-8601}
```

- `billed`는 턴마다 입력·캐시 생성·출력 토큰을 합한 값이다. 긴 대기(예: Notion 쓰기 승인 대기)로 프롬프트 캐시가
  만료되면 전체 맥락이 다시 만들어져 다시 더해진다.
- `read`는 매 턴 다시 읽은 캐시 토큰, `peak`는 한 턴의 최대 맥락으로 서브에이전트 완료 알림이 보고하는 토큰 수에
  가깝다.
- 2026-10-13~15 실측: 편집 `peak` 약 37만~47만, 감사 약 26만~34만. 후보 360개(10-13)와 180개(10-14·15)의
  차이가 작아 비용은 후보 수보다 묶음 전체 읽기와 선정 이슈 본문 대조가 좌우한다. 10-13 재감사는 승인 대기
  580초 동안 캐시가 만료되어 `billed`가 약 56만으로 `peak`의 두 배였다.

### 토큰 절감 장치 (2026-10-16)

모두 정밀성 규칙은 그대로 두고 읽는 분량·중복을 줄이는 장치다. 효과는 위 측정으로 확인한다.

- 서브에이전트는 `daily-routine.md` 전체 대신 [`subagent-rules.md`](subagent-rules.md)와 각자의 지침만 읽는다.
  기록으로 보면 문서 읽기는 컨텍스트의 작은 부분이라(감사 지침·루틴 문서 합계 약 2만 자) 절감 폭은 작다.
- 제출 후 확인은 페이지 전체 재열람 대신 `mode: "rows"`로 제목 중복·`날짜` 속성만 본다.
- `scripts/draft_grounding_check.py`는 초안의 수치·직함·기관명·인용을 근거 후보와 기계적으로 대조하고
  `--preflight`로 finalize와 같은 스키마·계획·문형 검증을 제출 전에 돌린다. 감사는 이 결과를 먼저 보고
  의심 항목부터 근거로 확인한다. 이 검사는 힌트일 뿐 발행 판정이나 감사를 대체하지 않는다.

## Notion 쿼리 도구 한도 초과

증상: 연결된 Notion 도구 `notion-query-data-sources`가 `usage_limit_reached`(Query Data Source usage
limit)를 반환한다. 원인은 SQL 모드에 걸린 요금제의 워크스페이스 공유 한도다(Business·Enterprise와
Notion AI에서는 무제한). 정확한 한도와 초기화 주기는 공개되어 있지 않고, 같은 워크스페이스의 다른
세션과 연결이 몫을 함께 쓴다. `rows` 모드, `notion-search`, `notion-fetch`와 쓰기 도구는 이 한도에
걸리지 않았다(2026-10-08 확인). 최종 발행은 GitHub Actions의 Notion API라 영향이 없다.

대응: SQL 모드를 쓰지 않는다(모드를 생략하면 SQL이다). `docs/subagent-rules.md` "Notion 조회 방법"의
순서(`rows` → 검색 → `fetch`)를 따른다. 한도를 완전히 없애려면 Notion Business 요금제가 필요하며 이는
유료 서비스 도입이라 사용자 승인이 먼저다.
