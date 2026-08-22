from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .models import ArticleRecord, Classification, StoreResult
from .utils import kst_date, stable_article_key


class ArticleStorage(ABC):
    """Storage boundary that can later be implemented by D1 or another database."""

    @abstractmethod
    def upsert(self, article: ArticleRecord) -> StoreResult:
        raise NotImplementedError

    @abstractmethod
    def iter_articles(self) -> Iterable[ArticleRecord]:
        raise NotImplementedError

    @abstractmethod
    def contains(self, source: str, canonical_url: str) -> bool:
        raise NotImplementedError


class JsonlStorage(ArticleStorage):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.articles_dir = root / "data" / "articles"
        self.review_dir = root / "data" / "review"
        self.state_path = root / "data" / "state" / "source_state.json"
        self.health_path = root / "health" / "latest.json"
        for directory in (
            self.articles_dir,
            self.review_dir,
            self.state_path.parent,
            self.health_path.parent,
            root / "reports",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, tuple[Path, ArticleRecord]] | None = None
        self._batch_depth = 0
        self._pending: dict[str, tuple[Path, ArticleRecord]] = {}
        self._pending_affected_paths: set[Path] = set()

    def _ensure_index(self) -> dict[str, tuple[Path, ArticleRecord]]:
        if self._index is not None:
            return self._index
        self._index = {}
        for path in sorted(self.articles_dir.glob("*.jsonl")):
            for record in self._read_records(path):
                key = self._key(record)
                existing = self._index.get(key)
                if existing is None or existing[1].last_seen_at < record.last_seen_at:
                    self._index[key] = (path, record)
        return self._index

    def contains(self, source: str, canonical_url: str) -> bool:
        probe = stable_article_key(source, canonical_url, None, "", None)
        return probe in self._ensure_index()

    def delete_by_source_article_ids(self, source: str, article_ids: list[str]) -> int:
        """Delete exact API cache records confirmed absent by a refresh response."""

        wanted = frozenset(article_ids)
        if not wanted:
            return 0
        index = self._ensure_index()
        targets = {
            key: (path, record)
            for key, (path, record) in index.items()
            if record.source == source and record.article_id in wanted
        }
        affected = {path for path, _record in targets.values()}
        for path in affected:
            records = [
                record
                for record in self._read_records(path)
                if not (record.source == source and record.article_id in wanted)
            ]
            self._write_records(path, records)
            self._rebuild_review(path.stem, records)
        for key in targets:
            index.pop(key, None)
        return len(targets)

    def upsert(self, article: ArticleRecord) -> StoreResult:
        index = self._ensure_index()
        key = self._key(article)
        destination = (
            self.articles_dir / f"{kst_date(article.published_at or article.first_seen_at)}.jsonl"
        )
        existing_pair = index.get(key)
        result = StoreResult.NEW
        affected = {destination}
        if existing_pair:
            old_path, existing = existing_pair
            affected.add(old_path)
            article.first_seen_at = min(existing.first_seen_at, article.first_seen_at)
            article.discovery_route = list(
                dict.fromkeys([*existing.discovery_route, *article.discovery_route])
            )
            semantic_changed = self._semantic_payload(existing) != self._semantic_payload(article)
            result = StoreResult.UPDATED if semantic_changed else StoreResult.DUPLICATE

        if self._batch_depth:
            self._pending[key] = (destination, article)
            self._pending_affected_paths.update(affected)
            index[key] = (destination, article)
            return result

        records_by_path: dict[Path, list[ArticleRecord]] = {
            path: self._read_records(path) for path in affected
        }
        for path, records in records_by_path.items():
            records_by_path[path] = [record for record in records if self._key(record) != key]
        records_by_path[destination].append(article)
        for path, records in records_by_path.items():
            records.sort(key=self._sort_key)
            self._write_records(path, records)
            self._rebuild_review(path.stem, records)
        index[key] = (destination, article)
        return result

    @contextmanager
    def batch(self) -> Iterator[JsonlStorage]:
        """Commit a collection run with at most one rewrite per affected date file."""

        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._flush_pending()

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        pending_keys = set(self._pending)
        records_by_path = {
            path: [
                record
                for record in self._read_records(path)
                if self._key(record) not in pending_keys
            ]
            for path in self._pending_affected_paths
        }
        for destination, article in self._pending.values():
            records_by_path.setdefault(destination, []).append(article)
        for path, records in records_by_path.items():
            records.sort(key=self._sort_key)
            self._write_records(path, records)
            self._rebuild_review(path.stem, records)
        self._pending.clear()
        self._pending_affected_paths.clear()

    def iter_articles(self) -> Iterable[ArticleRecord]:
        for path in sorted(self.articles_dir.glob("*.jsonl")):
            yield from self._read_records(path)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 1, "sources": {}}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def update_source_state(self, source: str, values: dict[str, Any]) -> None:
        state = self.load_state()
        state.setdefault("version", 1)
        sources = state.setdefault("sources", {})
        sources[source] = {**sources.get(source, {}), **values}
        self.atomic_write_json(self.state_path, state)

    def write_health(self, payload: dict[str, Any]) -> None:
        self.atomic_write_json(self.health_path, payload)

    @staticmethod
    def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2, default=_json_default)
            temporary.write("\n")
            temp_path = Path(temporary.name)
        os.replace(temp_path, path)

    @staticmethod
    def _read_records(path: Path) -> list[ArticleRecord]:
        if not path.exists():
            return []
        records: list[ArticleRecord] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(ArticleRecord.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"invalid JSONL record at {path}:{line_number}: {exc}") from exc
        return records

    @staticmethod
    def _write_records(path: Path, records: list[ArticleRecord]) -> None:
        if not records:
            if path.exists():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
        ) as temporary:
            for record in records:
                temporary.write(record.model_dump_json(exclude_none=False) + "\n")
            temp_path = Path(temporary.name)
        os.replace(temp_path, path)

    def _rebuild_review(self, date_name: str, records: list[ArticleRecord]) -> None:
        review_path = self.review_dir / f"{date_name}.jsonl"
        review = [record for record in records if record.classification == Classification.REVIEW]
        self._write_records(review_path, review)

    @staticmethod
    def _key(record: ArticleRecord) -> str:
        return stable_article_key(
            record.source,
            record.canonical_url,
            record.article_id,
            record.title,
            record.published_at,
        )

    @staticmethod
    def _sort_key(record: ArticleRecord) -> tuple[str, str, str]:
        published = (record.published_at or record.first_seen_at).astimezone(UTC).isoformat()
        return published, record.source, record.canonical_url

    @staticmethod
    def _semantic_payload(record: ArticleRecord) -> dict[str, Any]:
        payload = record.model_dump(mode="json")
        payload.pop("first_seen_at", None)
        payload.pop("last_seen_at", None)
        return payload


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"not JSON serializable: {type(value)!r}")
