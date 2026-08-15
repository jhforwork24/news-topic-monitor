from __future__ import annotations

from pathlib import Path

KST_NAME = "Asia/Seoul"
USER_AGENT_NAME = "KCILNewsMonitor/0.1"
DEFAULT_REQUEST_INTERVAL_SECONDS = 2.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 2
MAX_SUMMARY_CHARS = 500
MAX_ERROR_CHARS = 500


def project_root() -> Path:
    current = Path.cwd().resolve()
    if (current / "config" / "topics.yml").is_file():
        return current
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "config" / "topics.yml").is_file():
        return source_root
    raise FileNotFoundError("project root not found; run from the repository root or pass --root")
