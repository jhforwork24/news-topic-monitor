"""Replay "동일 주제 이전 보도" selection against published briefings.

Usage:
  PYTHONPATH=src python scripts/replay_previous_coverage.py <repo_root> <since YYYY-MM-DD>

Prints, per published issue, what the archived briefing showed and what the
current code selects, with the matched tokens and rule for each pick.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_topic_monitor.briefing import (
    labor_previous_history,
    previous_coverage_for,
    previous_coverage_pool,
)
from news_topic_monitor.models import Classification
from news_topic_monitor.storage import JsonlStorage

KST = timezone(timedelta(hours=9))
WINDOW = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})부터 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})까지")


def parse_report(text: str):
    window = WINDOW.search(text)
    start = (
        datetime.strptime(window.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST) if window else None
    )
    issues = []
    section = "?"
    current = None
    mode = None
    for line in text.splitlines():
        m = re.match(r"^# (I{1,3})\.", line)
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^## \d+\. (.*)", line)
        if m:
            current = {"section": section, "title": m.group(1), "urls": [], "published": []}
            issues.append(current)
            mode = None
            continue
        if line.startswith("### 주요 언론 보도"):
            mode = "articles"
            continue
        if line.startswith("### "):
            mode = None
            continue
        if "<summary>동일 주제 이전 보도</summary>" in line:
            mode = "previous"
            continue
        if line.startswith("</details>"):
            mode = None
            continue
        if current is None:
            continue
        urls = re.findall(r"\]\((https?://[^)\s]+)\)", line)
        if mode == "articles" and line.startswith("- ") and urls:
            current["urls"].append(urls[-1])
        elif mode == "previous" and line.startswith("| [") and urls:
            current["published"].append(urls[0])
    return start, issues


def main() -> None:
    root = Path(sys.argv[1])
    since = sys.argv[2]
    storage = JsonlStorage(root)
    index = {record.canonical_url: record for _, record in storage._ensure_index().values()}
    all_articles = list(index.values())
    total = changed = shown_before = shown_after = 0
    for path in sorted((root / "reports" / "briefings").glob("*.md")):
        if path.stem < since:
            continue
        start, issues = parse_report(path.read_text(encoding="utf-8"))
        if start is None:
            continue
        history = previous_coverage_pool(
            item
            for item in all_articles
            if (item.published_at or item.first_seen_at) < start
            and item.classification == Classification.RELEVANT
        )
        labor = previous_coverage_pool(
            labor_previous_history(
                all_articles, start=start, topics_path=root / "config" / "topics.yml"
            )
        )
        for issue in issues:
            articles = [index[url] for url in issue["urls"] if url in index]
            if not articles:
                continue
            total += 1
            picks = previous_coverage_for(articles, labor if issue["section"] == "II" else history)
            before = issue["published"]
            after = [p.url for p in picks]
            shown_before += bool(before)
            shown_after += bool(after)
            if set(before) != set(after):
                changed += 1
            print(f"\n=== {path.stem} [{issue['section']}] {issue['title'][:70]}")
            print(
                f"  published({len(before)}): "
                + ", ".join(u.rsplit("/", 1)[-1][:24] for u in before)
            )
            for p in picks:
                tokens = "·".join(p.matched_tokens[:6])
                print(f"  -> {p.published} {p.outlet} | {p.label[:50]} | {p.match_rule} | {tokens}")
    print(
        f"\nissues={total} with_previous_before={shown_before}"
        f" with_previous_now={shown_after} changed={changed}"
    )


if __name__ == "__main__":
    main()
