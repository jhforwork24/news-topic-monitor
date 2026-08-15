from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from .adapters import ALL_ADAPTERS
from .adapters.hani import HaniAdapter
from .adapters.mbc import MbcAdapter
from .briefing import build_briefing, write_briefing
from .classifier import RuleClassifier
from .constants import project_root
from .http import SafeHttpClient
from .notion_publish import (
    NotionConfigurationError,
    NotionPublisher,
    NotionPublishSettings,
    write_notion_health,
)
from .pipeline import Collector
from .reporting import generate_report
from .settings import ContactRequiredError, Settings
from .storage import JsonlStorage
from .utils import KST, parse_datetime

LOGGER = logging.getLogger(__name__)
YOUTUBE_REFRESH_AFTER = timedelta(days=28)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="robots.txt-compliant news topic monitor")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="project root (default: current repository root)",
    )
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="collect a recent time window")
    collect.add_argument("--since-hours", type=float, default=6.0)
    collect.add_argument("--start", help="inclusive ISO-8601 start")
    collect.add_argument("--end", help="exclusive ISO-8601 end")
    collect.add_argument(
        "--sources", nargs="*", choices=[adapter.source for adapter in ALL_ADAPTERS]
    )

    backfill = subparsers.add_parser("backfill", help="recheck the recent 48 hours")
    backfill.add_argument("--hours", type=float, default=48.0)
    backfill.add_argument("--end", help="exclusive ISO-8601 end")
    backfill.add_argument(
        "--sources", nargs="*", choices=[adapter.source for adapter in ALL_ADAPTERS]
    )

    report = subparsers.add_parser("report", help="generate a KST daily report")
    report.add_argument("--date", help="KST report end date YYYY-MM-DD; default today")
    report.add_argument("--start", help="inclusive ISO-8601 override")
    report.add_argument("--end", help="exclusive ISO-8601 override")

    briefing = subparsers.add_parser(
        "briefing", help="generate the briefing (section IV is conditional)"
    )
    _add_report_window_arguments(briefing)

    publish = subparsers.add_parser("publish-notion", help="publish a managed briefing to Notion")
    _add_report_window_arguments(publish)
    publish.add_argument(
        "--dry-run", action="store_true", help="render the briefing without calling Notion"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = (args.root or project_root()).resolve()
    if args.command == "report":
        return _report(args, root)
    if args.command in {"briefing", "publish-notion"}:
        return _briefing(args, root)
    try:
        settings = Settings.from_env(root)
    except ContactRequiredError as exc:
        LOGGER.error("%s; no network request was made", exc)
        return 2
    return _collect(args, settings)


def _collect(args: argparse.Namespace, settings: Settings) -> int:
    end = parse_datetime(args.end) if args.end else datetime.now(UTC)
    assert end is not None
    if args.command == "collect" and args.start:
        start = parse_datetime(args.start)
    else:
        hours = args.since_hours if args.command == "collect" else args.hours
        start = end - timedelta(hours=hours)
    assert start is not None
    if start >= end:
        raise SystemExit("start must be earlier than end")
    selected = set(args.sources or [])
    storage = JsonlStorage(settings.root)
    adapters = []
    for adapter_type in ALL_ADAPTERS:
        if selected and adapter_type.source not in selected:
            continue
        if adapter_type is HaniAdapter:
            adapters.append(HaniAdapter(settings.hani_max_pages))
        elif adapter_type is MbcAdapter:
            stale_ids = storage.stale_article_ids(
                "mbc", before=datetime.now(UTC) - YOUTUBE_REFRESH_AFTER
            )
            adapters.append(MbcAdapter(settings.youtube_api_key, refresh_video_ids=stale_ids))
        else:
            adapters.append(adapter_type())
    classifier = RuleClassifier(settings.root / "config" / "topics.yml")
    with SafeHttpClient(settings) as http:
        health = Collector(
            http=http,
            storage=storage,
            classifier=classifier,
            adapters=adapters,
            max_discovery_children=settings.max_discovery_children,
        ).run(start, end)
    print(json.dumps(health.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 1 if health.all_sources_failed else 0


def _report(args: argparse.Namespace, root: Path) -> int:
    date_value, start, end = _report_window(args)
    path = generate_report(
        JsonlStorage(root), start=start, end=end, report_date=date_value.isoformat()
    )
    print(path)
    return 0


def _briefing(args: argparse.Namespace, root: Path) -> int:
    date_value, start, end = _report_window(args)
    storage = JsonlStorage(root)
    document = build_briefing(
        storage,
        topics_path=root / "config" / "topics.yml",
        start=start,
        end=end,
        report_date=date_value.isoformat(),
    )
    path = write_briefing(
        document,
        output_path=root / "reports" / "briefings" / f"{date_value.isoformat()}.md",
        # The repository may be public. The private reference URL is injected only
        # into Notion blocks by NotionPublisher, never into committed Markdown.
        crpd_url=None,
    )
    print(path)
    if args.command == "briefing" or args.dry_run:
        return 0
    try:
        settings = NotionPublishSettings.from_env()
        with NotionPublisher(settings) as publisher:
            try:
                result = publisher.publish(document)
            except Exception as exc:
                try:
                    report_url = publisher.record_failure(date_value.isoformat(), str(exc))
                except Exception as report_exc:
                    LOGGER.error("could not record Notion failure report: %s", report_exc)
                    report_url = None
                write_notion_health(
                    root,
                    report_date=date_value.isoformat(),
                    status="failed",
                    page_url=report_url,
                    error=str(exc),
                )
                raise
        write_notion_health(
            root,
            report_date=date_value.isoformat(),
            status=result.status,
            page_url=result.page_url,
        )
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return 0
    except NotionConfigurationError as exc:
        LOGGER.error("%s", exc)
        write_notion_health(
            root,
            report_date=date_value.isoformat(),
            status="configuration_error",
            error=str(exc),
        )
        return 2


def _add_report_window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", help="KST report end date YYYY-MM-DD; default today")
    parser.add_argument("--start", help="inclusive ISO-8601 override")
    parser.add_argument("--end", help="exclusive ISO-8601 override")


def _report_window(args: argparse.Namespace):
    now_kst = datetime.now(KST)
    date_value = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now_kst.date()
    default_end = datetime.combine(date_value, time(hour=9), tzinfo=KST).astimezone(UTC)
    end = parse_datetime(args.end) if args.end else default_end
    start = parse_datetime(args.start) if args.start else end - timedelta(days=1)
    assert start is not None and end is not None
    if start >= end:
        raise SystemExit("start must be earlier than end")
    return date_value, start, end


if __name__ == "__main__":
    sys.exit(main())
