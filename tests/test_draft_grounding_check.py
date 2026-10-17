import importlib.util
import json
import sys
from pathlib import Path


def _load():
    path = Path(__file__).parents[1] / "scripts" / "draft_grounding_check.py"
    spec = importlib.util.spec_from_file_location("draft_grounding_check", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EVIDENCE = (
    "정은경 보건복지부 장관은 4,620만 원의 과태료를 부과했다고 밝혔다. "
    "최근 5년간 217명이 주사를 맞았다. 노조는 “검진 한두 달 전에 반복 투약했다”고 주장했다."
)


def _candidates(tmp_path, *, wrap: bool = False):
    payload = {
        "candidates": [
            {"candidate_id": "a" * 16, "title": "제목", "summary": "", "evidence_text": EVIDENCE}
        ]
    }
    path = tmp_path / "part.json"
    if wrap:
        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _draft(tmp_path, summary: str, *, ids=None, exclusions=None):
    draft = {
        "plan": {
            "issues": [
                {
                    "section": "labor",
                    "title": "이슈",
                    "candidate_ids": ids or ["a" * 16],
                    "summary": summary,
                    "tone_analysis": "",
                }
            ],
            "exclusions": exclusions or [],
        }
    }
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return path


def _run(module, tmp_path, summary, capsys, **kwargs):
    code = module.main(
        [
            "--draft",
            str(_draft(tmp_path, summary, **kwargs)),
            "--candidates",
            str(_candidates(tmp_path)),
            "--strict",
        ]
    )
    return code, capsys.readouterr().out


def test_clean_draft_reports_no_findings(tmp_path, capsys) -> None:
    summary = "보건복지부 장관은 4620만 원 과태료를 부과했고 217명이 맞았다."
    code, out = _run(_load(), tmp_path, summary, capsys)
    assert code == 0
    assert "GROUNDING_CLEAN" in out


def test_figure_unit_title_entity_quote_and_ids_are_reported(tmp_path, capsys) -> None:
    summary = (
        "노동부 위원장은 4630만 원과 217건을 말했고 서울청년재단이 “근거 없는 말입니다”라고 했다."
    )
    exclusions = [{"candidate_id": "a" * 16, "reason": "x"}, {"candidate_id": "b" * 16}]
    code, out = _run(_load(), tmp_path, summary, capsys, exclusions=exclusions)
    assert code == 1
    for kind in ("figure:", "unit:", "title:", "entity:", "quote:", "선정과 중복", "대기열에 없음"):
        assert kind in out
    assert EVIDENCE[:10] not in out  # the report never echoes article text


def test_unknown_candidate_in_issue_is_reported(tmp_path, capsys) -> None:
    code, out = _run(_load(), tmp_path, "내용", capsys, ids=["c" * 16])
    assert code == 1
    assert "id:" in out


def test_notion_fetch_wrapper_is_unwrapped(tmp_path) -> None:
    module = _load()
    candidates = module.load_candidates([_candidates(tmp_path, wrap=True)])
    assert set(candidates) == {"a" * 16}


def _queue(tmp_path):
    from news_topic_monitor.chat_bridge import editorial_queue_id
    from news_topic_monitor.models import EditorialCandidate

    candidate = EditorialCandidate.model_validate(
        {
            "candidate_id": "a" * 64,
            "source": "beminor",
            "canonical_url": "https://www.beminor.com/news/articleView.html?idxno=1",
            "title": "장애인 이동권 보장 조례 제정",
            "byline": None,
            "section": None,
            "published_at": "2026-10-12T00:00:00Z",
            "summary": None,
            "evidence_text": "서울시의회가 장애인 이동권 조례를 통과시켰다. " * 10,
            "body_status": "fetched",
            "verification_status": "body_verified",
            "rule_classification": "relevant",
            "rule_score": 80.0,
        }
    )
    part = tmp_path / "part.json"
    part.write_text(
        json.dumps({"candidates": [candidate.model_dump(mode="json")]}), encoding="utf-8"
    )
    return part, editorial_queue_id([candidate])


def _preflight_draft(tmp_path, queue_id, **issue):
    base = {
        "section": "disability",
        "title": "서울시의회 장애인 이동권 조례 통과",
        "keyword": "이동권 조례",
        "candidate_ids": ["a" * 64],
        "summary": "서울시의회가 장애인 이동권 조례를 통과시켰다.",
        "tone_analysis": "",
        "subsection": None,
    }
    base.update(issue)
    draft = {
        "schema_version": 1,
        "report_date": "2026-10-13",
        "queue_id": queue_id,
        "draft_id": "draft-20261013-070000",
        "submitted_at": "2026-10-13T07:00:00+09:00",
        "plan": {"issues": [base], "exclusions": []},
    }
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return path


def test_preflight_passes_a_valid_draft_and_reports_finalize_side_errors(tmp_path, capsys) -> None:
    module = _load()
    part, queue_id = _queue(tmp_path)

    good = _preflight_draft(tmp_path, queue_id)
    ok = module.main(["--draft", str(good), "--candidates", str(part), "--preflight", "--strict"])
    assert ok == 0
    assert "preflight_errors=0" in capsys.readouterr().out

    bad = _preflight_draft(
        tmp_path,
        queue_id,
        keyword="서울시의회 장애인 이동권 조례 통과",
        tone_analysis="첫 문장이다. 둘째 문장이다.",
        summary="에이블뉴스는 조례를 통과시켰다고 전했다",
    )
    code = module.main(["--draft", str(bad), "--candidates", str(part), "--preflight", "--strict"])
    out = capsys.readouterr().out
    assert code == 1
    for expected in ("키워드 요약이 제목을 그대로", "단일 보도 논조", "완성형 문장", "언론사"):
        assert expected in out

    wrong_queue = _preflight_draft(tmp_path, "0" * 64)
    module.main(["--draft", str(wrong_queue), "--candidates", str(part), "--preflight"])
    assert "queue_id:" not in capsys.readouterr().out  # digest check is opt-in
    module.main(
        ["--draft", str(wrong_queue), "--candidates", str(part), "--preflight", "--check-queue-id"]
    )
    assert "queue_id: 입력 후보 1개의 digest" in capsys.readouterr().out
