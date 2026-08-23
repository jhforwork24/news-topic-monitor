from pathlib import Path

import yaml


def _workflow(name: str) -> dict:
    root = Path(__file__).parents[1]
    with (root / ".github" / "workflows" / name).open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=yaml.BaseLoader)


def test_connected_claude_bridge_has_one_scheduled_final_writer() -> None:
    collect = _workflow("collect.yml")
    backfill = _workflow("backfill.yml")
    report = _workflow("report.yml")
    editorial = _workflow("editorial-publish.yml")
    queue = _workflow("editorial-queue.yml")
    finalize = _workflow("editorial-finalize.yml")
    fallback = _workflow("publish-notion.yml")

    assert collect["on"]["schedule"] == [{"cron": "17 2-23/3 * * *"}]
    assert backfill["on"]["schedule"] == [{"cron": "20 22 * * *"}]
    assert report["on"]["schedule"] == [{"cron": "2 0 * * *"}]
    assert "schedule" not in editorial["on"]
    assert queue["on"]["schedule"] == [{"cron": "5 0 * * *"}]
    assert finalize["on"]["schedule"] == [{"cron": "48 0 * * *"}]
    assert "schedule" not in fallback["on"]
    assert "PUBLICATION_OWNER" in finalize["jobs"]["editorial-finalize"]["if"]
    assert "CHAT_EDITORIAL_BRIDGE_ENABLED" in queue["jobs"]["editorial-queue"]["if"]
    queue_env = queue["jobs"]["editorial-queue"]["env"]
    assert "NAVER_API_HUB_CLIENT_ID" in queue_env
    assert "NAVER_API_HUB_CLIENT_SECRET" in queue_env
    assert "NOTION_QUEUE_DATA_SOURCE_ID" in queue_env
    env = editorial["jobs"]["editorial-publish"]["env"]
    assert "NAVER_API_HUB_CLIENT_ID" in env
    assert "NAVER_API_HUB_CLIENT_SECRET" in env
    assert "OPENAI_AUDITOR_MODEL" in env
    assert "OPENAI_EDITOR_CHUNK_SIZE" in env
    assert "OPENAI_EDITOR_BODY_FETCH_LIMIT_PER_SOURCE" in env
    finalize_env = finalize["jobs"]["editorial-finalize"]["env"]
    assert "OPENAI_API_KEY" not in finalize_env
    assert "NAVER_API_HUB_CLIENT_ID" in finalize_env
    assert "NAVER_API_HUB_CLIENT_SECRET" in finalize_env
    assert "NOTION_QUEUE_DATA_SOURCE_ID" in finalize_env
    assert "NOTION_REPORTS_DATA_SOURCE_ID" in finalize_env


def test_data_writers_checkout_latest_branch_after_concurrency_wait() -> None:
    job_names = {
        "collect.yml": "collect",
        "backfill.yml": "backfill",
        "report.yml": "report",
        "editorial-publish.yml": "editorial-publish",
        "editorial-queue.yml": "editorial-queue",
        "editorial-finalize.yml": "editorial-finalize",
        "publish-notion.yml": "publish",
    }

    for workflow_name, job_name in job_names.items():
        workflow = _workflow(workflow_name)
        checkout = workflow["jobs"][job_name]["steps"][0]
        assert checkout["uses"] == "actions/checkout@v7"
        assert checkout["with"]["ref"] == "${{ github.ref_name }}"
        assert checkout["with"]["fetch-depth"] == "0"
