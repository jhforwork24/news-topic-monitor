# 운영 점검 절차

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
   다음 예약 실행의 재시도를 기다린다.
   404/410(robots.txt 없음)도 기본은 fail-closed다. 사용자가 승인해
   `config/source-registry.yaml`에 `robots_absent_policy: allow_if_absent`를 둔 출처(현재
   참세상만)에 한해 RFC 9309에 따라 "robots.txt 없음 = 제한 없음"으로 읽고, health
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

`editorial-queue.yml`의 예약은 05:10 KST지만 GitHub 예약 트리거 자체가 1.5~2.5시간 늦게
발동하는 일이 흔하다(드물게 8시간). 기다리지 말고 workflow_dispatch로 직접 실행한다
(`date=YYYY-MM-DD`, `dry_run=false`). 실행은 보통 10~15분 걸린다.

- 이미 오늘 매니페스트가 있으면 그 실행은 `status=already_built`로 아무것도 하지 않고 정상
  종료한다(health의 status도 같은 값). 실패가 아니므로 그대로 두면 된다. 뒤늦게 발동한 예약
  실행이 진행 중인 작업을 덮어쓰지 못하게 막는 장치다.
- `force` 입력은 이 보호를 끄고 대기열을 새로 만든다. 새 queue_id가 생기므로 그 시점에 이미
  제출된 초안은 finalize에서 queue_id 불일치로 거부된다. 초안을 버릴 의도가 분명할 때만 쓴다.

## 고정 칼럼 누락

발행 게이트가 "대기열에 있는 고정 칼럼이 초안에 선정도 제외도 되지 않음"으로 막으면, 그날
한겨레 `세계의 창`(지제크)·미디어스 김민하·경향 `고병권의 묵묵` 중 하나가 실제로 게재됐는데
초안이 처리하지 않은 것이다. 해당 candidate_id를 `opinion` 이슈에 넣거나, 싣지 않는 이유를
`plan.exclusions`에 남긴 뒤 다시 발행한다. 게이트를 우회하지 않는다.

이 세 칼럼은 필자명이 매체마다 다른 필드에 있다(경향은 제목, 한겨레·미디어스는 요약의 바이라인
줄). 그래서 수집 단계에서 제목만으로 irrelevant 판정돼도 본문을 확인하고, 대기열 상한과 무관하게
후보로 고정 배정하며, 후보 줄에 `III절 고정 칼럼` 힌트를 붙인다. 이 힌트가 없으면 그날 그
칼럼이 게재되지 않았다는 뜻이지 후보가 걸러졌다는 뜻이 아니다.

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
