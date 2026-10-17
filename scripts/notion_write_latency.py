#!/usr/bin/env python3
"""Report how long Notion write-tool calls, and any other subagent tool call, waited.

A write call that sits unanswered for minutes is almost always waiting for an
interactive approval nobody is there to give (connector tool permission), which
stalls the scheduled routine. The same can happen to any other tool: on 2026-10-17 a
trivial subagent Bash call (a file clean-up) returned after 3,619 seconds, which cost
an hour and expired the prompt cache. So every non-Notion-write tool call of a
subagent that takes longer than ``--stall-threshold`` seconds (or never returns) is
reported as ``STALL``. Reads the local session transcripts only and prints tool name,
timing and agent id: never page titles, inputs or any content.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
WRITE_TOOL = re.compile(
    r"^mcp__.+__notion-(create-pages|update-page|create-comment|move-pages|duplicate-page|"
    r"create-database|update-data-source|create-view|update-view|create-folder|update-folder)$"
)
DEFAULT_THRESHOLD_SECONDS = 60.0
DEFAULT_STALL_SECONDS = 120.0


@dataclass(frozen=True)
class WriteCall:
    started_at: datetime
    tool: str
    agent: str
    seconds: float | None  # None: no result in the log (lost, e.g. container restart)
    is_write: bool = True  # False: any other subagent tool call (watched only for stalls)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def transcript_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/*.jsonl"))


def collect(paths: Iterable[Path], since: datetime) -> list[WriteCall]:
    calls: list[WriteCall] = []
    for path in paths:
        agent = path.stem.removeprefix("agent-")[:8] if "subagents" in path.parts else "main"
        pending: dict[str, tuple[str, datetime, bool]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                content = (row.get("message") or {}).get("content")
                stamp = _parse(row["timestamp"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = str(block.get("name", ""))
                    is_write = bool(WRITE_TOOL.match(name))
                    # Only subagents are watched for other tools: the main session waits on
                    # purpose (background polling, subagent runs).
                    if is_write or agent != "main":
                        pending[block["id"]] = (name.split("__")[-1], stamp, is_write)
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                    tool, started, is_write = pending.pop(block["tool_use_id"])
                    if started >= since:
                        calls.append(
                            WriteCall(
                                started, tool, agent, (stamp - started).total_seconds(), is_write
                            )
                        )
        for tool, started, is_write in pending.values():
            if started >= since:
                calls.append(WriteCall(started, tool, agent, None, is_write))
    return sorted(calls, key=lambda call: call.started_at)


def render(
    calls: list[WriteCall], threshold: float, now: datetime, stall_threshold: float
) -> tuple[list[str], int, int]:
    """Lines to print, Notion write waits, and other-tool stalls."""

    lines: list[str] = []
    waits = 0
    stalls = 0
    for call in calls:
        when = call.started_at.astimezone(KST).strftime("%m-%d %H:%M:%S")
        limit = threshold if call.is_write else stall_threshold
        if call.seconds is None:
            age = (now - call.started_at).total_seconds()
            late = age > limit
            flag = ("UNANSWERED" if call.is_write else "STALL") if late else "pending"
            detail = f"no_result age={int(age)}s"
        else:
            late = call.seconds > limit
            flag = ("WAIT" if call.is_write else "STALL") if late else "ok"
            detail = f"{int(call.seconds)}s"
        if not call.is_write and not late:
            continue  # ordinary tool calls are never listed
        if late:
            if call.is_write:
                waits += 1
            else:
                stalls += 1
        lines.append(f"{when} KST {call.tool} agent={call.agent} {detail} {flag}")
    return lines, waits, stalls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO-8601 start (default: 6 hours ago)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_SECONDS)
    parser.add_argument(
        "--stall-threshold",
        type=float,
        default=DEFAULT_STALL_SECONDS,
        help="seconds after which a non-Notion-write subagent tool call counts as a stall",
    )
    parser.add_argument("--root", type=Path, default=Path.home() / ".claude" / "projects")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    since = _parse(args.since) if args.since else now - timedelta(hours=6)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    calls = collect(transcript_files(args.root), since)
    lines, waits, stalls = render(calls, args.threshold, now, args.stall_threshold)
    print("\n".join(lines) if lines else "no Notion write calls found")

    def longest(write: bool) -> int:
        return int(
            max(
                (
                    c.seconds if c.seconds is not None else (now - c.started_at).total_seconds()
                    for c in calls
                    if c.is_write == write
                ),
                default=0,
            )
        )

    if waits:
        print(f"WAITS_DETECTED count={waits} longest={longest(True)}s")
    else:
        print("NO_WAITS")
    if stalls:
        print(f"STALLS_DETECTED count={stalls} longest={longest(False)}s")
    else:
        print("NO_STALLS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
