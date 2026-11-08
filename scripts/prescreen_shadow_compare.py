"""Compare queue prescreen shadow decisions with the published selection.

Usage:
  python scripts/prescreen_shadow_compare.py --date 2026-11-10 [--write]
  python scripts/prescreen_shadow_compare.py --all

For each date it reads ``health/editorial_queue/prescreen_shadow/DATE.json``
(what a model-free prescreen would have removed from the editor's reading) and
``evidence/DATE.json`` (which queue candidates the published briefing used).
A would-exclude candidate that was published is a recall miss.

``--write`` stores the per-date result under
``health/editorial_queue/prescreen_shadow/compare/DATE.json``. The command never
fails the caller's workflow on missing inputs; it exits 1 only when a recall
miss is found, so a human can see it in the run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from news_topic_monitor.prescreen import compare_shadow, shadow_compare_path
from news_topic_monitor.storage import JsonlStorage
from news_topic_monitor.utils import KST


def _dates(root: Path) -> list[str]:
    directory = root / "health" / "editorial_queue" / "prescreen_shadow"
    return sorted(path.stem for path in directory.glob("*.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--date", help="report date YYYY-MM-DD (default: today in KST)")
    group.add_argument("--all", action="store_true", help="every date with a shadow record")
    parser.add_argument("--write", action="store_true", help="store the per-date result")
    args = parser.parse_args(argv)

    dates = _dates(args.root) if args.all else [args.date or datetime.now(KST).date().isoformat()]
    results = [compare_shadow(args.root, report_date) for report_date in dates]
    if args.write:
        for result in results:
            if result["status"] != "no_shadow_record":
                JsonlStorage.atomic_write_json(
                    shadow_compare_path(args.root, result["report_date"]), result
                )

    compared = [r for r in results if r["status"] in ("no_overlap", "recall_miss")]
    summary = {
        "dates": len(results),
        "compared_dates": len(compared),
        "would_exclude_total": sum(r["would_exclude_count"] or 0 for r in compared),
        "recall_miss_total": sum(len(r["overlap"]) for r in compared),
        "status_by_date": {r["report_date"]: r["status"] for r in results},
    }
    print(
        json.dumps(
            {
                "summary": summary,
                "misses": [
                    {"report_date": r["report_date"], **miss}
                    for r in results
                    for miss in r["overlap"]
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 1 if summary["recall_miss_total"] else 0


if __name__ == "__main__":
    sys.exit(main())
