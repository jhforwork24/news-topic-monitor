from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx

from .adapters import ALL_ADAPTERS
from .adapters.base import SourceAdapter
from .adapters.hani import HaniAdapter
from .adapters.mbc import MbcAdapter
from .assurance import (
    PublishGateDecision,
    build_evidence_manifest,
    evaluate_census,
    evaluate_publish_gate,
    write_assurance_outputs,
)
from .briefing import BriefingDocument, build_briefing, build_editorial_briefing, write_briefing
from .briefing_validation import BriefingValidationError, validate_briefing
from .chat_bridge import ChatEditorialQueueManifest, validate_chat_editorial_bridge
from .classifier import RuleClassifier
from .constants import project_root
from .editorial import (
    EditorialApiError,
    EditorialConfigurationError,
    EditorialEvidenceStore,
    EditorialRun,
    EditorialValidationError,
    OpenAIEditorialClient,
    OpenAIEditorialSettings,
    select_chat_editorial_candidates,
    write_editorial_failure,
    write_editorial_health,
)
from .final_state import revalidate_final_state
from .gap_detection import (
    NaverApiError,
    NaverConfigurationError,
    NaverSearchClient,
    NaverSearchSettings,
    run_gap_detection,
    run_reverse_search,
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
from .policy import (
    PolicyConfigurationError,
    load_briefing_policy,
    load_source_registry,
    validate_policy_contract,
)
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

    publish = subparsers.add_parser(
        "publish-notion", help="publish a same-date-idempotent briefing to Notion"
    )
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

    finalize = subparsers.add_parser(
        "editorial-finalize",
        help="validate connected ChatGPT draft/audit, recrawl, gate, and publish",
    )
    _add_report_window_arguments(finalize)
    finalize.add_argument(
        "--dry-run", action="store_true", help="run final validation without calling Notion"
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
    if args.command == "editorial-finalize":
        return _editorial_finalize(args, settings)
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
    command_started = perf_counter()
    phase_durations: dict[str, float] = {}
    try:
        editorial_settings = OpenAIEditorialSettings.from_env()
        source_registry = load_source_registry(settings.root / "config" / "source-registry.yaml")
        briefing_policy = load_briefing_policy(settings.root / "config" / "briefing-policy.yaml")
        validate_policy_contract(source_registry, briefing_policy)
        try:
            naver_settings = NaverSearchSettings.from_env()
            naver_configuration_error = None
        except NaverConfigurationError as exc:
            naver_settings = None
            naver_configuration_error = str(exc)
        preflight_started = perf_counter()
        preflight_health: dict[str, object] = {
            "report_date": report_date,
            "naver_api_hub": {"status": "pending", "error": None},
            "openai": {"status": "pending", "error": None},
        }
        if naver_settings is None:
            preflight_health["naver_api_hub"] = {
                "status": "degraded",
                "error": naver_configuration_error,
            }
        else:
            try:
                with NaverSearchClient(naver_settings) as naver:
                    naver.search("장애인", display=1)
                preflight_health["naver_api_hub"] = {
                    "status": "complete",
                    "error": None,
                }
            except NaverApiError as exc:
                preflight_health["naver_api_hub"] = {
                    "status": "degraded",
                    "error": str(exc),
                }
        try:
            with OpenAIEditorialClient(editorial_settings) as preflight_editor:
                preflight_editor.preflight()
            preflight_health["openai"] = {"status": "complete", "error": None}
        except (EditorialApiError, EditorialValidationError) as exc:
            preflight_health["openai"] = {"status": "failed", "error": str(exc)}
            phase_durations["api_preflight"] = perf_counter() - preflight_started
            _write_api_preflight_health(settings.root, preflight_health)
            raise
        phase_durations["api_preflight"] = perf_counter() - preflight_started
        _write_api_preflight_health(settings.root, preflight_health)
        runner_temp = os.getenv("RUNNER_TEMP", "").strip() or None
        with TemporaryDirectory(
            prefix="news-topic-editorial-", dir=runner_temp
        ) as temporary_directory:
            database_path = Path(temporary_directory) / "candidates.sqlite3"
            with EditorialEvidenceStore(database_path) as evidence_store:
                classifier = RuleClassifier(settings.root / "config" / "topics.yml")
                LOGGER.info("editorial phase=initial_collection status=started")
                phase_started = perf_counter()
                with SafeHttpClient(settings) as http:
                    health = Collector(
                        http=http,
                        storage=storage,
                        classifier=classifier,
                        adapters=adapters,
                        max_discovery_children=settings.max_discovery_children,
                        evidence_store=evidence_store,
                        capture_all_bodies=True,
                        # The 48-hour overlap is for late discovery. Fetching every
                        # body is limited to the 24-hour report window; rule-matched
                        # older candidates are still refreshed by Collector.
                        capture_body_start=start,
                        capture_body_limit_per_source=(
                            editorial_settings.body_fetch_limit_per_source
                        ),
                    ).run(collection_start, end)
                phase_durations["initial_collection"] = perf_counter() - phase_started
                LOGGER.info(
                    "editorial phase=initial_collection status=completed duration_seconds=%.3f",
                    phase_durations["initial_collection"],
                )
                if health.all_sources_failed:
                    raise EditorialValidationError("모든 출처 수집에 실패하여 편집을 중단함")

                candidates = evidence_store.candidates(start=start, end=end)
                LOGGER.info(
                    "editorial phase=gpt_edit_audit status=started candidate_count=%d",
                    len(candidates),
                )
                phase_started = perf_counter()
                with OpenAIEditorialClient(editorial_settings) as editor:
                    run = editor.edit(candidates)
                draft_completed_at = datetime.now(UTC)
                phase_durations["gpt_edit_audit"] = perf_counter() - phase_started
                LOGGER.info(
                    "editorial phase=gpt_edit_audit status=completed duration_seconds=%.3f",
                    phase_durations["gpt_edit_audit"],
                )

                candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
                selected_sources = {
                    candidate_by_id[candidate_id].source
                    for issue in run.plan.issues
                    for candidate_id in issue.candidate_ids
                    if candidate_id in candidate_by_id
                }
                if not selected_sources:
                    raise EditorialValidationError(
                        "최종상태 공식 재수집에 사용할 선정 기사 출처가 없음"
                    )
                revalidation_requested_at = datetime.now(UTC)
                if revalidation_requested_at > end + timedelta(hours=6):
                    raise EditorialValidationError(
                        "보고 경계가 6시간 이상 지나 발행 직전 최종상태를 완전하게 재검증할 수 없음"
                    )
                revalidation_start = (
                    end
                    if revalidation_requested_at > end
                    else max(start, revalidation_requested_at - timedelta(hours=1))
                )
                LOGGER.info(
                    "editorial phase=final_state_recrawl status=started sources=%s",
                    ",".join(sorted(selected_sources)),
                )
                phase_started = perf_counter()
                revalidation_adapters = _build_adapters(settings, storage, selected_sources)
                with SafeHttpClient(settings) as http:
                    revalidation_health = Collector(
                        http=http,
                        storage=storage,
                        classifier=classifier,
                        adapters=revalidation_adapters,
                        max_discovery_children=settings.max_discovery_children,
                        evidence_store=evidence_store,
                        capture_all_bodies=True,
                        capture_body_start=revalidation_start,
                        capture_body_limit_per_source=(
                            editorial_settings.body_fetch_limit_per_source
                        ),
                        write_health=False,
                        rolling_window_end=True,
                    ).run(revalidation_start, revalidation_requested_at)
                phase_durations["final_state_recrawl"] = perf_counter() - phase_started
                LOGGER.info(
                    "editorial phase=final_state_recrawl status=completed duration_seconds=%.3f",
                    phase_durations["final_state_recrawl"],
                )

                revalidation_end = revalidation_health.run_finished_at
                all_candidates = evidence_store.candidates(start=start, end=revalidation_end)
                known_canonical_urls = {candidate.canonical_url for candidate in all_candidates}
                LOGGER.info("editorial phase=gap_reverse_search status=started")
                phase_started = perf_counter()

                if naver_settings is None:
                    gap_detection = run_gap_detection(
                        client=None,
                        configuration_error=naver_configuration_error,
                        policy=briefing_policy,
                        registry=source_registry,
                        known_canonical_urls=known_canonical_urls,
                        start=start,
                        end=end,
                    )
                    reverse_search = run_reverse_search(
                        client=None,
                        configuration_error=naver_configuration_error,
                        plan=run.plan,
                        policy=briefing_policy,
                        registry=source_registry,
                        start=start,
                        end=end,
                    )
                else:
                    with NaverSearchClient(naver_settings) as naver:
                        gap_detection = run_gap_detection(
                            client=naver,
                            configuration_error=None,
                            policy=briefing_policy,
                            registry=source_registry,
                            known_canonical_urls=known_canonical_urls,
                            start=start,
                            end=end,
                        )
                        reverse_search = run_reverse_search(
                            client=naver,
                            configuration_error=None,
                            plan=run.plan,
                            policy=briefing_policy,
                            registry=source_registry,
                            start=start,
                            end=end,
                        )
                phase_durations["gap_reverse_search"] = perf_counter() - phase_started
                LOGGER.info(
                    "editorial phase=gap_reverse_search status=completed duration_seconds=%.3f",
                    phase_durations["gap_reverse_search"],
                )

                phase_started = perf_counter()
                census = evaluate_census(
                    health,
                    window_start=start,
                    registry=source_registry,
                    policy=briefing_policy,
                )
                final_state = revalidate_final_state(
                    plan=run.plan,
                    audit=run.audit,
                    all_candidates=all_candidates,
                    health=revalidation_health,
                    policy=briefing_policy,
                    draft_completed_at=draft_completed_at,
                    checked_at=revalidation_health.run_finished_at,
                )
                gate = evaluate_publish_gate(
                    report_date=report_date,
                    policy=briefing_policy,
                    census=census,
                    gap_detection=gap_detection,
                    reverse_search=reverse_search,
                    final_state=final_state,
                    audit=run.audit,
                    plan=run.plan,
                    candidates=candidates,
                    health=health,
                    revalidation_health=revalidation_health,
                )
                phase_durations["assurance"] = perf_counter() - phase_started

            # The database remains confined to this temporary directory and is
            # removed automatically before the command returns.
            current_articles = [
                article
                for article in storage.iter_articles()
                if start <= (article.published_at or article.first_seen_at) < revalidation_end
            ]
            manifest = build_evidence_manifest(
                report_date=report_date,
                articles=current_articles,
                plan=run.plan,
                census=census,
                gap_detection=gap_detection,
                reverse_search=reverse_search,
                final_state=final_state,
            )
            write_assurance_outputs(settings.root, manifest=manifest, gate=gate)
            if not gate.allowed:
                phase_durations["total"] = perf_counter() - command_started
                _record_gate_failure(settings.root, gate)
                error = "publish gate blocked publication: " + "; ".join(gate.fatal_errors)
                write_editorial_health(
                    settings.root,
                    run,
                    report_date=report_date,
                    status="blocked_by_publish_gate",
                    error=error,
                    phase_durations_seconds=phase_durations,
                )
                LOGGER.error("%s", error)
                return 3

            phase_started = perf_counter()
            document = build_editorial_briefing(
                storage,
                plan=run.plan,
                start=start,
                end=end,
                report_date=report_date,
            )
            document.source_failures.extend(
                (
                    f"원인={item.cause} · 대체경로={item.fallback} · "
                    f"결과={item.result} · 다음조치={item.next_action}"
                )
                for item in gate.reporting_items
            )
            validate_briefing(document)
            path = write_briefing(
                document,
                output_path=(settings.root / "reports" / "briefings" / f"{report_date}.md"),
                crpd_url=None,
            )
            phase_durations["render_briefing"] = perf_counter() - phase_started
            phase_durations["total"] = perf_counter() - command_started
            write_editorial_health(
                settings.root,
                run,
                report_date=report_date,
                phase_durations_seconds=phase_durations,
            )
            print(path)
            if args.dry_run:
                return 0
            return _publish_to_notion(document, settings.root)
    except (EditorialConfigurationError, PolicyConfigurationError) as exc:
        LOGGER.error("%s", exc)
        phase_durations["total"] = perf_counter() - command_started
        write_editorial_failure(
            settings.root,
            report_date=report_date,
            status="configuration_error",
            error=str(exc),
            phase_durations_seconds=phase_durations,
        )
        return 2
    except (EditorialApiError, EditorialValidationError, BriefingValidationError) as exc:
        LOGGER.error("%s", exc)
        phase_durations["total"] = perf_counter() - command_started
        write_editorial_failure(
            settings.root,
            report_date=report_date,
            status="failed",
            error=str(exc),
            phase_durations_seconds=phase_durations,
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
        source_registry = load_source_registry(settings.root / "config" / "source-registry.yaml")
        briefing_policy = load_briefing_policy(settings.root / "config" / "briefing-policy.yaml")
        validate_policy_contract(source_registry, briefing_policy)
        queue_settings = EditorialQueueSettings.from_env()
        runner_temp = os.getenv("RUNNER_TEMP", "").strip() or None
        with TemporaryDirectory(
            prefix="news-topic-chat-editorial-", dir=runner_temp
        ) as temporary_directory:
            database_path = Path(temporary_directory) / "candidates.sqlite3"
            with EditorialEvidenceStore(database_path) as evidence_store:
                classifier = RuleClassifier(settings.root / "config" / "topics.yml")
                labor_classifier = RuleClassifier(
                    settings.root / "config" / "topics.yml", topic="labor_care_poverty"
                )
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
                try:
                    naver_settings = NaverSearchSettings.from_env()
                    naver_configuration_error = None
                except NaverConfigurationError as exc:
                    naver_settings = None
                    naver_configuration_error = str(exc)
                known_canonical_urls = {
                    article.canonical_url for article in storage.iter_articles()
                } | {candidate.canonical_url for candidate in candidates}
                if naver_settings is None:
                    gap_detection = run_gap_detection(
                        client=None,
                        configuration_error=naver_configuration_error,
                        policy=briefing_policy,
                        registry=source_registry,
                        known_canonical_urls=known_canonical_urls,
                        start=start,
                        end=end,
                    )
                else:
                    with NaverSearchClient(naver_settings) as naver:
                        gap_detection = run_gap_detection(
                            client=naver,
                            configuration_error=None,
                            policy=briefing_policy,
                            registry=source_registry,
                            known_canonical_urls=known_canonical_urls,
                            start=start,
                            end=end,
                        )
                _write_api_preflight_health(
                    settings.root,
                    {
                        "report_date": report_date,
                        "openai": {
                            "status": "not_required",
                            "error": None,
                            "route": "connected_chatgpt_automation",
                        },
                        "naver_api_hub": {
                            "status": gap_detection.status.value,
                            "error": "; ".join(gap_detection.errors) or None,
                            "queries_attempted": gap_detection.queries_attempted,
                            "queries_completed": gap_detection.queries_completed,
                            "potential_gap_count": len(gap_detection.potential_gaps),
                        },
                    },
                )
                if args.dry_run:
                    write_editorial_queue_health(
                        settings.root,
                        report_date=report_date,
                        status="dry_run",
                        candidate_count=len(verified),
                        part_count=(len(verified) + queue_settings.chunk_size - 1)
                        // queue_settings.chunk_size,
                        gap_detection_status=gap_detection.status.value,
                        gap_potential_count=len(gap_detection.potential_gaps),
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
                        initial_health_finished_at=health.run_finished_at,
                        gap_detection=gap_detection,
                        queue_settings=queue_settings,
                        labor_classifier=labor_classifier,
                        source_failures=_run_source_failures(health),
                    )
                _write_initial_health_snapshot(
                    settings.root,
                    report_date=report_date,
                    queue_id=result.queue_id,
                    health=health,
                )

        write_editorial_queue_health(
            settings.root,
            report_date=report_date,
            status=result.status,
            candidate_count=result.candidate_count,
            part_count=result.part_count,
            queue_id=result.queue_id,
            gap_detection_status=result.gap_detection_status,
            gap_potential_count=result.gap_potential_count,
        )
        # The manifest URL is intentionally printed only to the private Actions log.
        print(json.dumps(result.log_payload(), ensure_ascii=False, indent=2))
        return 0
    except (NotionConfigurationError, PolicyConfigurationError) as exc:
        LOGGER.error("%s", exc)
        write_editorial_queue_health(
            settings.root,
            report_date=report_date,
            status="configuration_error",
            error=str(exc),
        )
        _record_bridge_failure(settings.root, report_date, str(exc), "대기열 생성")
        return 2
    except (NotionApiError, EditorialQueueValidationError) as exc:
        LOGGER.error("%s", exc)
        write_editorial_queue_health(
            settings.root,
            report_date=report_date,
            status="failed",
            error=str(exc),
        )
        _record_bridge_failure(settings.root, report_date, str(exc), "대기열 생성")
        return 3


def _editorial_finalize(args: argparse.Namespace, settings: Settings) -> int:
    date_value, start, end = _report_window(args)
    report_date = date_value.isoformat()
    command_started = perf_counter()
    phase_durations: dict[str, float] = {}
    storage = JsonlStorage(settings.root)
    try:
        source_registry = load_source_registry(settings.root / "config" / "source-registry.yaml")
        briefing_policy = load_briefing_policy(settings.root / "config" / "briefing-policy.yaml")
        validate_policy_contract(source_registry, briefing_policy)
        try:
            naver_settings = NaverSearchSettings.from_env()
            naver_configuration_error = None
        except NaverConfigurationError as exc:
            naver_settings = None
            naver_configuration_error = str(exc)

        phase_started = perf_counter()
        preflight_health: dict[str, object] = {
            "report_date": report_date,
            "naver_api_hub": {"status": "pending", "error": None},
            "openai": {
                "status": "not_required",
                "error": None,
                "route": "connected_chatgpt_automation",
            },
        }
        if naver_settings is None:
            preflight_health["naver_api_hub"] = {
                "status": "degraded",
                "error": naver_configuration_error,
            }
        else:
            try:
                with NaverSearchClient(naver_settings) as naver:
                    naver.search("장애인", display=1)
                preflight_health["naver_api_hub"] = {"status": "complete", "error": None}
            except NaverApiError as exc:
                preflight_health["naver_api_hub"] = {
                    "status": "degraded",
                    "error": str(exc),
                }
        phase_durations["api_preflight"] = perf_counter() - phase_started
        _write_api_preflight_health(settings.root, preflight_health)

        phase_started = perf_counter()
        queue_notion_settings = NotionPublishSettings.from_queue_env()
        with NotionPublisher(queue_notion_settings) as queue_publisher:
            bundle = queue_publisher.load_chat_editorial_bridge(report_date)
        validate_chat_editorial_bridge(bundle)
        initial_health = _load_bound_initial_health(settings.root, bundle.queue.manifest)
        phase_durations["bridge_import_validation"] = perf_counter() - phase_started

        run = EditorialRun(
            model="connected_chatgpt_editor",
            auditor_model="connected_chatgpt_independent_auditor",
            candidates=bundle.queue.candidates,
            assessments=[],
            plan=bundle.draft.plan,
            audit=bundle.audit.audit,
        )
        candidate_by_id = {
            candidate.candidate_id: candidate for candidate in bundle.queue.candidates
        }
        selected_sources = {
            candidate_by_id[candidate_id].source
            for issue in run.plan.issues
            for candidate_id in issue.candidate_ids
            if candidate_id in candidate_by_id
        }
        if not selected_sources:
            raise EditorialValidationError("최종상태 공식 재수집에 사용할 선정 기사 출처가 없음")

        revalidation_requested_at = datetime.now(UTC)
        if revalidation_requested_at > end + timedelta(hours=6):
            raise EditorialValidationError(
                "보고 경계가 6시간 이상 지나 발행 직전 최종상태를 완전하게 재검증할 수 없음"
            )
        revalidation_start = (
            end
            if revalidation_requested_at > end
            else max(start, revalidation_requested_at - timedelta(hours=1))
        )
        runner_temp = os.getenv("RUNNER_TEMP", "").strip() or None
        with TemporaryDirectory(
            prefix="news-topic-chat-finalize-", dir=runner_temp
        ) as temporary_directory:
            database_path = Path(temporary_directory) / "revalidation.sqlite3"
            with EditorialEvidenceStore(database_path) as evidence_store:
                classifier = RuleClassifier(settings.root / "config" / "topics.yml")
                revalidation_adapters = _build_adapters(settings, storage, selected_sources)
                LOGGER.info(
                    "editorial phase=final_state_recrawl status=started sources=%s",
                    ",".join(sorted(selected_sources)),
                )
                phase_started = perf_counter()
                with SafeHttpClient(settings) as http:
                    revalidation_health = Collector(
                        http=http,
                        storage=storage,
                        classifier=classifier,
                        adapters=revalidation_adapters,
                        max_discovery_children=settings.max_discovery_children,
                        evidence_store=evidence_store,
                        capture_all_bodies=True,
                        capture_body_start=revalidation_start,
                        capture_body_limit_per_source=24,
                        write_health=False,
                        rolling_window_end=True,
                    ).run(revalidation_start, revalidation_requested_at)
                phase_durations["final_state_recrawl"] = perf_counter() - phase_started
                revalidation_end = revalidation_health.run_finished_at
                recrawled_candidates = evidence_store.candidates(start=start, end=revalidation_end)

            merged_candidates = {
                candidate.candidate_id: candidate for candidate in bundle.queue.candidates
            }
            merged_candidates.update(
                {candidate.candidate_id: candidate for candidate in recrawled_candidates}
            )
            all_candidates = list(merged_candidates.values())
            known_canonical_urls = {candidate.canonical_url for candidate in all_candidates} | {
                article.canonical_url for article in storage.iter_articles()
            }

            phase_started = perf_counter()
            if naver_settings is None:
                gap_detection = run_gap_detection(
                    client=None,
                    configuration_error=naver_configuration_error,
                    policy=briefing_policy,
                    registry=source_registry,
                    known_canonical_urls=known_canonical_urls,
                    start=start,
                    end=end,
                )
                reverse_search = run_reverse_search(
                    client=None,
                    configuration_error=naver_configuration_error,
                    plan=run.plan,
                    policy=briefing_policy,
                    registry=source_registry,
                    start=start,
                    end=end,
                )
            else:
                with NaverSearchClient(naver_settings) as naver:
                    gap_detection = run_gap_detection(
                        client=naver,
                        configuration_error=None,
                        policy=briefing_policy,
                        registry=source_registry,
                        known_canonical_urls=known_canonical_urls,
                        start=start,
                        end=end,
                    )
                    reverse_search = run_reverse_search(
                        client=naver,
                        configuration_error=None,
                        plan=run.plan,
                        policy=briefing_policy,
                        registry=source_registry,
                        start=start,
                        end=end,
                    )
            phase_durations["gap_reverse_search"] = perf_counter() - phase_started

            phase_started = perf_counter()
            census = evaluate_census(
                initial_health,
                window_start=start,
                registry=source_registry,
                policy=briefing_policy,
            )
            final_state = revalidate_final_state(
                plan=run.plan,
                audit=run.audit,
                all_candidates=all_candidates,
                health=revalidation_health,
                policy=briefing_policy,
                draft_completed_at=bundle.audit.submitted_at,
                checked_at=revalidation_health.run_finished_at,
            )
            gate = evaluate_publish_gate(
                report_date=report_date,
                policy=briefing_policy,
                census=census,
                gap_detection=gap_detection,
                reverse_search=reverse_search,
                final_state=final_state,
                audit=run.audit,
                plan=run.plan,
                candidates=bundle.queue.candidates,
                health=initial_health,
                revalidation_health=revalidation_health,
            )
            phase_durations["assurance"] = perf_counter() - phase_started

        current_articles = [
            article
            for article in storage.iter_articles()
            if start <= (article.published_at or article.first_seen_at) < revalidation_end
        ]
        manifest = build_evidence_manifest(
            report_date=report_date,
            articles=current_articles,
            plan=run.plan,
            census=census,
            gap_detection=gap_detection,
            reverse_search=reverse_search,
            final_state=final_state,
        )
        write_assurance_outputs(settings.root, manifest=manifest, gate=gate)
        if not gate.allowed:
            phase_durations["total"] = perf_counter() - command_started
            _record_gate_failure(settings.root, gate)
            error = "publish gate blocked publication: " + "; ".join(gate.fatal_errors)
            write_editorial_health(
                settings.root,
                run,
                report_date=report_date,
                status="blocked_by_publish_gate",
                error=error,
                phase_durations_seconds=phase_durations,
            )
            LOGGER.error("%s", error)
            return 3

        phase_started = perf_counter()
        document = build_editorial_briefing(
            storage,
            plan=run.plan,
            start=start,
            end=end,
            report_date=report_date,
        )
        document.source_failures.extend(
            (
                f"원인={item.cause} · 대체경로={item.fallback} · "
                f"결과={item.result} · 다음조치={item.next_action}"
            )
            for item in gate.reporting_items
        )
        validate_briefing(document)
        path = write_briefing(
            document,
            output_path=settings.root / "reports" / "briefings" / f"{report_date}.md",
            crpd_url=None,
        )
        phase_durations["render_briefing"] = perf_counter() - phase_started
        phase_durations["total"] = perf_counter() - command_started
        write_editorial_health(
            settings.root,
            run,
            report_date=report_date,
            phase_durations_seconds=phase_durations,
        )
        print(path)
        if args.dry_run:
            return 0
        return _publish_to_notion(document, settings.root)
    except (
        EditorialConfigurationError,
        PolicyConfigurationError,
        NotionConfigurationError,
    ) as exc:
        LOGGER.error("%s", exc)
        phase_durations["total"] = perf_counter() - command_started
        write_editorial_failure(
            settings.root,
            report_date=report_date,
            status="configuration_error",
            error=str(exc),
            phase_durations_seconds=phase_durations,
        )
        _record_bridge_failure(settings.root, report_date, str(exc), "최종 검증")
        return 2
    except (
        EditorialValidationError,
        EditorialQueueValidationError,
        NotionApiError,
        BriefingValidationError,
    ) as exc:
        LOGGER.error("%s", exc)
        phase_durations["total"] = perf_counter() - command_started
        write_editorial_failure(
            settings.root,
            report_date=report_date,
            status="failed",
            error=str(exc),
            phase_durations_seconds=phase_durations,
        )
        _record_bridge_failure(settings.root, report_date, str(exc), "최종 검증")
        return 3


def _initial_health_snapshot_path(root: Path, report_date: str) -> Path:
    return root / "health" / "editorial_queue" / "initial_health" / f"{report_date}.json"


def _write_initial_health_snapshot(
    root: Path, *, report_date: str, queue_id: str, health: RunHealth
) -> None:
    """Freeze the health used to build today's queue under its own report-date path.

    ``health/latest.json`` is overwritten by every later ``collect``/``backfill``
    run (including delayed or manually re-dispatched ones), so the queue's
    ``initial_health_finished_at`` binding cannot be verified against it once
    time passes. This snapshot is written once per queue and is never touched
    by unrelated collection runs, so the finalizer can still verify the exact
    health the queue was built from even if the shared ``latest.json`` moved on.
    """

    JsonlStorage.atomic_write_json(
        _initial_health_snapshot_path(root, report_date),
        {
            "report_date": report_date,
            "queue_id": queue_id,
            "health": health.model_dump(mode="json"),
        },
    )


def _load_bound_initial_health(root: Path, manifest: ChatEditorialQueueManifest) -> RunHealth:
    path = _initial_health_snapshot_path(root, manifest.report_date)
    if not path.exists():
        raise EditorialQueueValidationError("초기 전수 수집 health 스냅샷이 없음")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise EditorialQueueValidationError("초기 전수 수집 health 스냅샷이 유효하지 않음") from exc
    if payload.get("queue_id") != manifest.queue_id:
        raise EditorialQueueValidationError(
            "초기 전수 수집 health 스냅샷이 다른 queue_id에 바인딩됨"
        )
    try:
        health = RunHealth.model_validate(payload.get("health"))
    except ValueError as exc:
        raise EditorialQueueValidationError("초기 전수 수집 health 스냅샷이 유효하지 않음") from exc
    if health.run_finished_at != manifest.initial_health_finished_at:
        raise EditorialQueueValidationError(
            "초기 전수 수집 health 스냅샷이 편집 대기열 생성에 사용된 수집 실행과 일치하지 않음"
        )
    if health.window_start > manifest.report_start or health.window_end < manifest.report_end:
        raise EditorialQueueValidationError(
            "초기 전수 수집 health가 편집 대기열의 보고 구간을 포함하지 않음"
        )
    return health


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


def _write_api_preflight_health(root: Path, payload: dict[str, object]) -> None:
    JsonlStorage.atomic_write_json(
        root / "health" / "api_preflight" / "latest.json",
        {**payload, "checked_at": datetime.now(UTC)},
    )


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


def _record_gate_failure(root: Path, gate: PublishGateDecision) -> None:
    """Best-effort report to the private reports data source; never publish a briefing."""

    try:
        settings = NotionPublishSettings.from_reports_env()
    except NotionConfigurationError:
        return
    details = ["publish gate가 최종 발행을 차단함"]
    details.extend(f"치명적 오류: {error}" for error in gate.fatal_errors)
    details.extend(
        (
            f"원인={item.cause}; 대체경로={item.fallback}; 결과={item.result}; "
            f"다음조치={item.next_action}"
        )
        for item in gate.reporting_items
    )
    try:
        with NotionPublisher(settings) as publisher:
            publisher.record_failure(gate.report_date, "\n".join(details))
    except (NotionApiError, httpx.HTTPError):
        LOGGER.exception("could not write publish-gate failure to Notion reports")


def _record_bridge_failure(root: Path, report_date: str, error: str, phase: str) -> None:
    """Best-effort structured failure report for queue/import errors."""

    del root  # The private reporting destination is configured only through secrets/env.
    try:
        settings = NotionPublishSettings.from_reports_env()
    except NotionConfigurationError:
        return
    detail = short_error(error) or "미분류 오류"
    message = "\n".join(
        [
            f"원인={phase} 실패: {detail}",
            "대체경로=동일 날짜의 결정론적 수집·health·대기열 상태를 보존하고 자동 발행을 중단함",
            "결과=최종 Notion 브리핑을 생성하거나 수정하지 않음",
            "다음조치=오류를 해결한 뒤 같은 queue_id 기준으로 편집과 독립 감사를 순서대로 재실행함",
        ]
    )
    try:
        with NotionPublisher(settings) as publisher:
            publisher.record_failure(report_date, message)
    except (NotionApiError, httpx.HTTPError):
        LOGGER.exception("could not write connected-bridge failure to Notion reports")


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
