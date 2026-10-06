#!/usr/bin/env python3
"""Report how long Notion write-tool calls waited in Claude session logs.

A write call that sits unanswered for minutes is almost always waiting for an
interactive approval nobody is there to give (connector tool permission), which
stalls the scheduled routine. Reads the local session transcripts only and prints
tool name, timing and agent id: never page titles, inputs or any content.
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


@dataclass(frozen=True)
class WriteCall:
    started_at: datetime
    tool: str
    agent: str
    seconds: float | None  # None: no result in the log (lost, e.g. container restart)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def transcript_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/*.jsonl"))


def collect(paths: Iterable[Path], since: datetime) -> list[WriteCall]:
    calls: list[WriteCall] = []
    for path in paths:
        agent = path.stem.removeprefix("agent-")[:8] if "subagents" in path.parts else "main"
        pending: dict[str, tuple[str, datetime]] = {}
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
                if block.get("type") == "tool_use" and WRITE_TOOL.match(block.get("name", "")):
                    pending[block["id"]] = (block["name"].split("__")[-1], stamp)
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                    tool, started = pending.pop(block["tool_use_id"])
                    if started >= since:
                        calls.append(
                            WriteCall(started, tool, agent, (stamp - started).total_seconds())
                        )
        for tool, started in pending.values():
            if started >= since:
                calls.append(WriteCall(started, tool, agent, None))
    return sorted(calls, key=lambda call: call.started_at)


def render(calls: list[WriteCall], threshold: float, now: datetime) -> tuple[list[str], int]:
    lines: list[str] = []
    waits = 0
    for call in calls:
        when = call.started_at.astimezone(KST).strftime("%m-%d %H:%M:%S")
        if call.seconds is None:
            age = (now - call.started_at).total_seconds()
            flag = "UNANSWERED" if age > threshold else "pending"
            waits += flag == "UNANSWERED"
            lines.append(
                f"{when} KST {call.tool} agent={call.agent} no_result age={int(age)}s {flag}"
            )
        else:
            flag = "WAIT" if call.seconds > threshold else "ok"
            waits += flag == "WAIT"
            lines.append(f"{when} KST {call.tool} agent={call.agent} {int(call.seconds)}s {flag}")
    return lines, waits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO-8601 start (default: 6 hours ago)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_SECONDS)
    parser.add_argument("--root", type=Path, default=Path.home() / ".claude" / "projects")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    since = _parse(args.since) if args.since else now - timedelta(hours=6)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    calls = collect(transcript_files(args.root), since)
    lines, waits = render(calls, args.threshold, now)
    print("\n".join(lines) if lines else "no Notion write calls found")
    if waits:
        longest = max((c.seconds or (now - c.started_at).total_seconds()) for c in calls)
        print(f"WAITS_DETECTED count={waits} longest={int(longest)}s")
    else:
        print("NO_WAITS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
