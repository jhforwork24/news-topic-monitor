"""Trace why an issue got (or missed) "동일 주제 이전 보도".

Usage:
  PYTHONPATH=src python scripts/trace_previous_coverage.py \
      --start "2026-10-24 05:00" --section II --keyword 특구 URL [URL ...]

Prints the current articles' match tokens, which earlier articles containing
``--keyword`` in the title enter or leave the history pool (and why), the
document frequency of chosen tokens, and the picks under the production pool
and under a widened pool (every allowed-source article, no classifier).
Reads only stored metadata; it does not fetch anything.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_topic_monitor import briefing as b
from news_topic_monitor.classifier import RuleClassifier
from news_topic_monitor.models import Classification
from news_topic_monitor.storage import JsonlStorage

KST = timezone(timedelta(hours=9))


def _published(article) -> datetime:
    return article.published_at or article.first_seen_at


def _show(picks) -> None:
    if not picks:
        print("  (none)")
    for pick in picks:
        tokens = "·".join(pick.matched_tokens[:6])
        label = pick.label[:45]
        print(f"  -> {pick.published} {pick.outlet} | {label} | {pick.match_rule} | {tokens}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--start", required=True, help="window start, KST 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--section", choices=["I", "II"], default="II")
    parser.add_argument("--keyword", required=True, help="title substring for earlier articles")
    parser.add_argument("--df", default="", help="comma-separated tokens whose pool DF to print")
    parser.add_argument("urls", nargs="+", help="canonical URLs of the issue's articles")
    args = parser.parse_args()

    root = Path(args.root)
    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    index = {
        record.canonical_url: record for _, record in JsonlStorage(root)._ensure_index().values()
    }
    articles = list(index.values())
    current = [index[url] for url in args.urls if url in index]
    print(f"current articles: {len(current)}/{len(args.urls)}")
    for article in current:
        print(f"  {article.title} | tokens: {sorted(b.previous_coverage_tokens(article))}")

    topics = root / "config" / "topics.yml"
    oldest = start - timedelta(days=b.PREVIOUS_COVERAGE_MAX_AGE_DAYS)
    if args.section == "II":
        classifier = RuleClassifier(topics, topic="labor_care_poverty")
        history = b.labor_previous_history(articles, start=start, topics_path=topics)
        allowed = b.LABOR_SECTION_ALLOWED_SOURCES
    else:
        classifier = None
        history = [
            a
            for a in articles
            if _published(a) < start and a.classification == Classification.RELEVANT
        ]
        allowed = None

    print(f"\n--- earlier articles with '{args.keyword}' in the title")
    earlier = [a for a in articles if args.keyword in a.title and _published(a) < start]
    for article in sorted(earlier, key=_published):
        reasons = []
        if _published(article) < oldest:
            reasons.append("too-old")
        if allowed is not None and article.source not in allowed:
            reasons.append("source")
        if classifier is not None:
            result = classifier.classify(
                title=article.title, summary=article.summary, section=article.section
            )
            if result.classification != Classification.RELEVANT:
                reasons.append(f"cls={result.classification.value}")
        elif article.classification != Classification.RELEVANT:
            reasons.append(f"cls={article.classification.value}")
        status = "IN" if not reasons else "OUT:" + ",".join(reasons)
        day = _published(article).astimezone(KST).strftime("%m-%d")
        print(f"{day} {article.source} {status} | {article.title[:55]}")

    pool = b.previous_coverage_pool(history)
    print(f"\npool size {len(history)}, anchor_max_df {pool.anchor_max_df}")
    for token in filter(None, args.df.split(",")):
        print(f"  DF {token}: {pool.document_frequency.get(token)}")

    print("\n--- picks with the production pool")
    _show(b.previous_coverage_for(current, pool))

    widened = [
        a
        for a in articles
        if oldest <= _published(a) < start and (allowed is None or a.source in allowed)
    ]
    print(f"\n--- picks with a widened pool (no classifier, {len(widened)} articles)")
    _show(b.previous_coverage_for(current, b.previous_coverage_pool(widened)))


if __name__ == "__main__":
    main()
