"""Last successfully fetched robots.txt per origin, for brief unreachable spells only.

2026-11-21 사용자 승인(A안). robots.txt를 새로 받으려다 시간초과·연결 오류·5xx로 받지
못했을 때(RFC 9309 §2.3.1.4 "unreachable"), 최근 ``ROBOTS_COPY_MAX_AGE`` 안에 정상(HTTP
200)으로 받아 둔 사본으로 허용 여부를 판단한다. RFC 9309 §2.4는 사본을 24시간까지 쓰고,
받을 수 없는 동안에는 그보다 오래 쓰는 것도 허용하지만, 여기서는 6시간으로 좁힌다.
2026-11-11 동아일보·11-17·11-21 프레시안 대기열 누락은 모두 1.5~3.5시간 전 정상 사본이
있었다. 403·429·해석 불가·사본 없음·사본 만료는 종전대로 수집을 멈춘다. 저장하는 것은
robots.txt 본문(규칙)과 받은 시각뿐이며 기사 본문은 저장하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

ROBOTS_COPY_MAX_AGE = timedelta(hours=6)
# 같은 내용이면 이 간격보다 자주 다시 쓰지 않는다(수집 회차마다 파일이 바뀌어 커밋이
# 충돌하는 것을 줄인다). 사본의 나이는 이만큼 실제보다 많게 기록될 수 있어 보수적이다.
ROBOTS_COPY_REFRESH = timedelta(minutes=30)
# RFC 9309 §2.5: 크롤러는 최소 500 KiB까지 해석해야 한다. 그보다 큰 파일은 사본을 두지 않는다.
ROBOTS_COPY_MAX_BYTES = 500 * 1024


@dataclass(frozen=True)
class RobotsCopy:
    origin: str
    robots_url: str
    text: str
    fetched_at: datetime


class RobotsCopyStore:
    """JSON file of the latest HTTP 200 robots.txt per origin (rules and fetch time only)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._entries: dict[str, dict[str, str]] | None = None

    def _load(self) -> dict[str, dict[str, str]]:
        if self._entries is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                entries = raw.get("origins", {}) if isinstance(raw, dict) else {}
                self._entries = {
                    origin: value
                    for origin, value in entries.items()
                    if isinstance(value, dict)
                    and isinstance(value.get("text"), str)
                    and isinstance(value.get("fetched_at"), str)
                    and isinstance(value.get("robots_url"), str)
                }
            except (OSError, ValueError):
                self._entries = {}
        return self._entries

    def get(self, origin: str) -> RobotsCopy | None:
        with self._lock:
            entry = self._load().get(origin)
            if entry is None:
                return None
            try:
                fetched_at = datetime.fromisoformat(entry["fetched_at"])
            except ValueError:
                return None
            if fetched_at.tzinfo is None:
                return None
            return RobotsCopy(origin, entry["robots_url"], entry["text"], fetched_at)

    def usable(self, origin: str, *, now: datetime | None = None) -> RobotsCopy | None:
        copy = self.get(origin)
        if copy is None:
            return None
        age = (now or datetime.now(UTC)) - copy.fetched_at
        if age < timedelta(0) or age > ROBOTS_COPY_MAX_AGE:
            return None
        return copy

    def record(
        self, origin: str, robots_url: str, text: str, *, fetched_at: datetime | None = None
    ) -> None:
        if len(text.encode("utf-8")) > ROBOTS_COPY_MAX_BYTES:
            return
        fetched_at = fetched_at or datetime.now(UTC)
        with self._lock:
            entries = self._load()
            old = entries.get(origin)
            if old and old.get("text") == text and old.get("robots_url") == robots_url:
                try:
                    previous = datetime.fromisoformat(old["fetched_at"])
                except ValueError:
                    previous = None
                if previous is not None and fetched_at - previous < ROBOTS_COPY_REFRESH:
                    return
            entries[origin] = {
                "robots_url": robots_url,
                "fetched_at": fetched_at.astimezone(UTC).isoformat(),
                "text": text,
            }
            self._write(entries)

    def _write(self, entries: dict[str, dict[str, str]]) -> None:
        from .storage import JsonlStorage

        JsonlStorage.atomic_write_json(
            self.path,
            {
                "schema_version": 1,
                "max_age_hours": ROBOTS_COPY_MAX_AGE.total_seconds() / 3600,
                "origins": dict(sorted(entries.items())),
            },
        )
