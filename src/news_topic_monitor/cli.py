from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters import ALL_ADAPTERS
from .adapters.base import SourceAdapter
from .adapters.hani import HaniAdapter
from .adapters.mbc import MbcAdapter
from .briefing import BriefingDocument, build_briefing, build_editorial_briefing, write_briefing
from .briefing_validation import BriefingValidationError, validate_briefing
from .classifier import RuleClassifier
from .constants import project_root
from .editorial import (
    EditorialApiError,
    EditorialConfigurationError,
    EditorialEvidenceStore,
    EditorialValidationError,
    OpenAIEditorialClient,
    OpenAIEditorialSettings,
    select_chat_editorial_candidates,
    write_editorial_failure,
    write_editorial_health,
)
from .http import SafeHttpClient
from .models import RunHealth
from .notion_publish import (
    EditorialQueueSettings,
    EditorialQueueValidationError,
    NotionApiError,
    NotionConfigurationError,
    NotionPublisher,
    NotionPublishSettings,
    write_editorial_queue_health,
    write_notion_health,
)
from .pipeline import Collector
from .reporting import generate_report
from .settings import ContactRequiredError, Settings
from .storage import JsonlStorage
from .utils import KST, parse_datetime, short_error

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

    publish = subparsers.add_parser("publish-notion", help="publish a versioned briefing to Notion")
    _add_report_window_arguments(publish)
    publish.add_argument(
        "--dry-run", action="store_true", help="render the briefing without calling Notion"
    )

    editorial = subparsers.add_parser(
        "editorial-publish",
        help="collect broad evidence, ask GPT to edit, validate, and publish to Notion",
    )
    _add_report_window_arguments(editorial)
    editorial.add_argument(
        "--collect-hours",
        type=float,
        default=48.0,
        help="overlapping evidence collection window before the report boundary",
    )
    editorial.add_argument(
        "--sources", nargs="*", choices=[adapter.source for adapter in ALL_ADAPTERS]
    )
    editorial.add_argument(
        "--dry-run", action="store_true", help="run GPT editing without calling Notion"
    )

    queue = subparsers.add_parser(
        "editorial-queue",
        help="collect verified evidence and export a temporary queue for ChatGPT",
    )
    _add_report_window_arguments(queue)
    queue.add_argument(
        "--collect-hours",
        type=float,
        default=48.0,
        help="overlapping evidence collection window before the report boundary",
    )
    queue.add_argument("--sources", nargs="*", choices=[adapter.source for adapter in ALL_ADAPTERS])
    queue.add_argument(
        "--dry-run", action="store_true", help="validate the queue without calling Notion"
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
    if args.command == "editorial-publish":
        return _editorial_publish(args, settings)
    if args.command == "editorial-queue":
        return _editorial_queue(args, settings)
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
    storage = JsonlStorage(settings.root)
    adapters = _build_adapters(settings, storage, set(args.sources or []))
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
    try:
        validate_briefing(document)
    except BriefingValidationError as exc:
        LOGGER.error("%s", exc)
        if args.command == "publish-notion":
            write_notion_health(
                root,
                report_date=date_value.isoformat(),
                status="validation_failed",
                error=str(exc),
            )
        return 3
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
    return _publish_to_notion(document, root)


def _editorial_publish(args: argparse.Namespace, settings: Settings) -> int:
    date_value, start, end = _report_window(args)
    report_date = date_value.isoformat()
    collection_start = end - timedelta(hours=args.collect_hours)
    if collection_start >= end:
        raise SystemExit("collect-hours must be greater than zero")

    storage = JsonlStorage(settings.root)
    adapters = _build_adapters(settings, storage, set(args.sources or []))
    try:
        editorial_settings = OpenAIEditorialSettings.from_env()
        runner_temp = os.getenv("RUNNER_TEMP", "").strip() or None
        with TemporaryDirectory(
            prefix="news-topic-editorial-", dir=runner_temp
        ) as temporary_directory:
            database_path = Path(temporary_directory) / "candidates.sqlite3"
            with EditorialEvidenceStore(database_path) as evidence_store:
                classifier = RuleClassifier(settings.root / "config" / "topics.yml")
                with SafeHttpClient(settings) as http:
                    health = Collector(
                        http=http,
                        storage=storage,
                        classifier=classifier,
                        adapters=adapters,
                        max_discovery_children=settings.max_discovery_children,
                        evidence_store=evidence_store,
                        capture_all_bodies=True,
                    ).run(collection_start, end)
                if health.all_sources_failed:
                    raise EditorialValidationError("모든 출처 수집에 실패하여 편집을 중단함")

                candidates = evidence_store.candidates(start=start, end=end)
                with OpenAIEditorialClient(editorial_settings) as editor:
                    run = editor.edit(candidates)

            # The database remains confined to this temporary directory and is
            # removed automatically before the command returns.
            document = build_editorial_briefing(
                storage,
                plan=run.plan,
                start=start,
                end=end,
                report_date=report_date,
            )
            validate_briefing(document)
            path = write_briefing(
                document,
                output_path=(settings.root / "reports" / "briefings" / f"{report_date}.md"),
                crpd_url=None,
            )
            write_editorial_health(settings.root, run, report_date=report_date)
            print(path)
            if args.dry_run:
                return 0
            return _publish_to_notion(document, settings.root)
    except EditorialConfigurationError as exc:
        LOGGER.error("%s", exc)
        write_editorial_failure(
            settings.root,
            report_date=report_date,
            status="configuration_error",
            error=str(exc),
        )
        return 2
    except (EditorialApiError, EditorialValidationError, BriefingValidationError) as exc:
        LOGGER.error("%s", exc)
        write_editorial_failure(
            settings.root,
            report_date=report_date,
            status="failed",
            error=str(exc),
        )
        return 3


def _editorial_queue(args: argparse.Namespace, settings: Settings) -> int:
    date_value, start, end = _report_window(args)
    report_date = date_value.isoformat()
    collection_start = end - timedelta(hours=args.collect_hours)
    if collection_start >= end:
        raise SystemExit("collect-hours must be greater than zero")

    storage = JsonlStorage(settings.root)
    adapters = _build_adapters(settings, storage, set(args.sources or []))
    try:
        queue_settings = EditorialQueueSettings.from_env()
        runner_temp = os.getenv("RUNNER_TEMP", "").strip() or None
        with TemporaryDirectory(
            prefix="news-topic-chat-editorial-", dir=runner_temp
        ) as temporary_directory:
            database_path = Path(temporary_directory) / "candidates.sqlite3"
            with EditorialEvidenceStore(database_path) as evidence_store:
                classifier = RuleClassifier(settings.root / "config" / "topics.yml")
                with SafeHttpClient(settings) as http:
                    health = Collector(
                        http=http,
                        storage=storage,
                        classifier=classifier,
                        adapters=adapters,
                        max_discovery_children=settings.max_discovery_children,
                        evidence_store=evidence_store,
                        capture_all_bodies=True,
                        capture_body_start=start,
                        capture_body_limit_per_source=(queue_settings.body_fetch_limit_per_source),
                    ).run(collection_start, end)
                if health.all_sources_failed:
                    raise EditorialQueueValidationError(
                        "모든 출처 수집에 실패하여 편집 대기열 생성을 중단함"
                    )
                candidates = evidence_store.candidates(start=start, end=end)
                verified = select_chat_editorial_candidates(
                    candidates, queue_settings.max_candidates
                )
                if not verified:
                    raise EditorialQueueValidationError(
                        "정확한 발행시각과 확인 가능한 본문이 있는 편집 후보가 없음"
                    )
                if args.dry_run:
                    write_editorial_queue_health(
                        settings.root,
                        report_date=report_date,
                        status="dry_run",
                        candidate_count=len(verified),
                        part_count=(len(verified) + queue_settings.chunk_size - 1)
                        // queue_settings.chunk_size,
                    )
                    print(
                        json.dumps(
                            {
                                "status": "dry_run",
                                "report_date": report_date,
                                "candidate_count": len(verified),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return 0

                notion_settings = NotionPublishSettings.from_queue_env()
                with NotionPublisher(notion_settings) as publisher:
                    result = publisher.publish_editorial_queue(
                        candidates,
                        report_date=report_date,
                        start=start,
                        end=end,
                        queue_settings=queue_settings,
                        source_failures=_run_source_failures(health),
                    )

        write_editorial_queue_health(
            settings.root,
            report_date=report_date,
            status=result.status,
            candidate_count=result.candidate_count,
            part_count=result.part_count,
        )
        # The manifest URL is intentionally printed only to the private Actions log.
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return 0
    except NotionConfigurationError as exc:
        LOGGER.error("%s", exc)
        write_editorial_queue_health(
            settings.root,
            report_date=report_date,
            status="configuration_error",
            error=str(exc),
        )
        return 2
    except (NotionApiError, EditorialQueueValidationError) as exc:
        LOGGER.error("%s", exc)
        write_editorial_queue_health(
            settings.root,
            report_date=report_date,
            status="failed",
            error=str(exc),
        )
        return 3


def _build_adapters(
    settings: Settings,
    storage: JsonlStorage,
    selected: set[str],
) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []
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
    return adapters


def _run_source_failures(health: RunHealth) -> list[str]:
    failures: list[str] = []
    for source, detail in health.sources.items():
        if detail.success:
            continue
        message = detail.errors[0] if detail.errors else detail.discovery_status.value
        failures.append(f"{source}: {short_error(message) or '확인 실패'}")
    return failures


def _publish_to_notion(document: BriefingDocument, root: Path) -> int:
    report_date = document.report_date
    try:
        settings = NotionPublishSettings.from_env()
        with NotionPublisher(settings) as publisher:
            try:
                result = publisher.publish(document)
                publisher.record_report(document, result)
            except Exception as exc:
                try:
                    report_url = publisher.record_failure(report_date, str(exc))
                except Exception as report_exc:
                    LOGGER.error("could not record Notion failure report: %s", report_exc)
                    report_url = None
                write_notion_health(
                    root,
                    report_date=report_date,
                    status="failed",
                    page_url=report_url,
                    error=str(exc),
                )
                raise
        write_notion_health(
            root,
            report_date=report_date,
            status=result.status,
            page_url=result.page_url,
            fingerprint=result.fingerprint,
            version=result.version,
        )
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return 0
    except NotionConfigurationError as exc:
        LOGGER.error("%s", exc)
        write_notion_health(
            root,
            report_date=report_date,
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
