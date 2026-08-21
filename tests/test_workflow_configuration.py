from pathlib import Path

import yaml


def _workflow(name: str) -> dict:
    root = Path(__file__).parents[1]
    with (root / ".github" / "workflows" / name).open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=yaml.BaseLoader)


def test_only_editorial_publish_is_a_scheduled_notion_writer() -> None:
    collect = _workflow("collect.yml")
    backfill = _workflow("backfill.yml")
    report = _workflow("report.yml")
    editorial = _workflow("editorial-publish.yml")
    queue = _workflow("editorial-queue.yml")
    fallback = _workflow("publish-notion.yml")

    assert collect["on"]["schedule"] == [{"cron": "17 2-23/3 * * *"}]
    assert backfill["on"]["schedule"] == [{"cron": "20 22 * * *"}]
    assert report["on"]["schedule"] == [{"cron": "2 0 * * *"}]
    assert editorial["on"]["schedule"] == [{"cron": "5 0 * * *"}]
    assert "schedule" not in queue["on"]
    assert "schedule" not in fallback["on"]
    assert "PUBLICATION_OWNER" in editorial["jobs"]["editorial-publish"]["if"]
    env = editorial["jobs"]["editorial-publish"]["env"]
    assert "NAVER_API_HUB_CLIENT_ID" in env
    assert "NAVER_API_HUB_CLIENT_SECRET" in env
    assert "OPENAI_AUDITOR_MODEL" in env
    assert "OPENAI_EDITOR_CHUNK_SIZE" in env
    assert "OPENAI_EDITOR_BODY_FETCH_LIMIT_PER_SOURCE" in env
