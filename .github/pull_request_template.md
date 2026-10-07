## 배경
<!-- 왜 필요한 변경인지, 근거(실사이트 확인·기록·사고)를 적는다. 개인 노션 URL·token·ID는 쓰지 않는다. -->

## 변경
<!-- 무엇을 바꿨는지. 코드·문서·설정·시험을 나눠 적는다. -->

## 확인 목록
- [ ] `pytest -m 'not smoke'`, `ruff check .`, `ruff format --check .` 통과
- [ ] 발행 검증·렌더링·편집 규칙(문장 수, 섹션·하위 주제 상한, 제외 개수 등)을 바꿨다면 `docs/claude-editorial-instructions.md`, `docs/claude-auditor-task.md`, `editorial.py` 프롬프트를 함께 고쳤고 `tests/test_rule_doc_sync.py`가 통과한다 (규칙만 바꾸고 지침을 그대로 두면 다음 예약 실행의 초안이 거부될 수 있다)
- [ ] 출처별 파서·선택자를 바꿨다면 실제 공식 사이트로 확인한 구조에 맞춰 최소 fixture와 단위시험을 갱신했다
- [ ] 기존 `data/`, `reports/`, `health/` 기록을 삭제·덮어쓰지 않았다
- [ ] robots.txt 준수, 유료 API·서버 도입 승인, 노션 단일 writer 등 `AGENTS.md` 필수 규칙을 어기지 않았다

## 검증
<!-- 실행한 시험과 결과, 실사이트·실행 확인 내용 -->
