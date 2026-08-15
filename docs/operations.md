# 운영 점검 절차

## 공통 원칙

`health/latest.json`의 `success`, `errors`, `structure_warnings`, `discovered`, `new`,
`duplicates`, `bodies_checked`, `bodies_blocked`를 먼저 확인한다. 기사 0건을 곧바로 보도 부재로
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
5. robots가 가리키는 새 공식 sitemap이 있다면 그 경로 자체의 허용 여부와 형식을 별도로 검증한
   뒤 어댑터 변경을 검토한다.

## GitHub Actions 실패

1. 세 워크플로에 `MONITOR_CONTACT` Repository variable이 노출되는지 확인한다. 값 자체를 로그에
   출력하지 않는다.
2. Actions의 job conclusion과 `health/latest.json`을 비교한다. 모든 출처 실패인지, 테스트·설치·
   push 단계 실패인지 분리한다.
3. 예약 누락이면 Collect를 수동으로 최근 6시간보다 넓게 실행하고 Daily backfill을 48시간으로
   실행한다.
4. 의존성 설치 오류는 `pyproject.toml`과 Python 3.12 여부를 확인한다. 무심코 major version 상한을
   제거하지 않는다.
5. 전체 출처 실패는 실패로 유지한다. 성공으로 가장하기 위해 `continue-on-error`를 파이프라인
   전체에 적용하지 않는다.

## 데이터 push 충돌

워크플로는 동일 concurrency 그룹으로 상호 직렬화되고 push 실패 시 최대 3회 `pull --rebase`한다.

1. rebase 충돌이 나면 자동으로 한쪽 JSONL을 덮어쓰지 않는다.
2. 양쪽 파일을 JSONL로 파싱해 canonical URL key 기준으로 합치고, `first_seen_at`은 이른 값,
   `last_seen_at`은 늦은 값을 유지한다.
3. 같은 key의 판별·메타데이터가 다르면 더 최근 `last_seen_at` 레코드를 기본으로 하되 본문이
   저장되지 않았는지 확인한다.
4. 날짜별 파일, review 파일, state와 health를 재생성한 뒤 멱등성 시험을 실행한다.
5. 강제 push는 사용하지 않는다.

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
