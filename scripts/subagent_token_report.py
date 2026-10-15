#!/usr/bin/env python3
"""Sum the token usage of Claude subagents (editor, auditor, ...) from session logs.

The daily routine spends most of its Claude tokens in the editor and auditor
subagents. This reads the local session transcripts only and prints, per agent,
its label (the Agent call description), timing, assistant turns, tool calls and
token totals: never prompts, page titles or any content.

Token columns:
  billed   sum over turns of input + cache-creation + output tokens. When a long
           pause (for example a Notion write waiting for approval) lets the prompt
           cache expire, the whole context is created again and counted again.
  read     cache-read tokens (the context re-read on every turn)
  peak     the largest context seen in one turn (input + cache creation + read);
           close to the token count a subagent's completion notice reports
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


@dataclass
class AgentUsage:
    agent: str
    label: str
    started_at: datetime
    finished_at: datetime
    turns: int = 0
    tool_calls: int = 0
    fresh_input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0
    peak_context: int = 0
    by_message: dict[str, dict[str, int]] = field(default_factory=dict, repr=False)

    @property
    def billed(self) -> int:
        return self.fresh_input + self.cache_creation + self.output


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def transcript_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*.jsonl")) + sorted(root.glob("*/*/subagents/*.jsonl"))


def _label(path: Path) -> str:
    meta = path.with_suffix(".meta.json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "main" if "subagents" not in path.parts else "unknown"
    description = data.get("description") if isinstance(data, dict) else None
    return str(description) if description else "unknown"


def read_agent(path: Path) -> AgentUsage | None:
    agent = path.stem.removeprefix("agent-")[:8] if "subagents" in path.parts else "main"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    usage: AgentUsage | None = None
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            stamp = _parse(row["timestamp"])
        except (ValueError, KeyError, TypeError):
            continue
        if usage is None:
            usage = AgentUsage(agent, _label(path), stamp, stamp)
        usage.finished_at = max(usage.finished_at, stamp)
        message = row.get("message")
        if row.get("type") != "assistant" or not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            usage.tool_calls += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
        raw = message.get("usage")
        message_id = message.get("id")
        if not isinstance(raw, dict) or not isinstance(message_id, str):
            continue
        # One API message is logged once per content block with the same id; the last
        # record carries the final output token count.
        usage.by_message[message_id] = {
            "input": int(raw.get("input_tokens") or 0),
            "creation": int(raw.get("cache_creation_input_tokens") or 0),
            "read": int(raw.get("cache_read_input_tokens") or 0),
            "output": int(raw.get("output_tokens") or 0),
        }
    if usage is None:
        return None
    for tokens in usage.by_message.values():
        usage.turns += 1
        usage.fresh_input += tokens["input"]
        usage.cache_creation += tokens["creation"]
        usage.cache_read += tokens["read"]
        usage.output += tokens["output"]
        usage.peak_context = max(
            usage.peak_context, tokens["input"] + tokens["creation"] + tokens["read"]
        )
    return usage


def collect(paths: Iterable[Path], since: datetime) -> list[AgentUsage]:
    agents = [read_agent(path) for path in paths]
    found = [item for item in agents if item is not None and item.started_at >= since]
    return sorted(found, key=lambda item: item.started_at)


def _minutes(item: AgentUsage) -> float:
    return (item.finished_at - item.started_at).total_seconds() / 60


def render(agents: list[AgentUsage], *, include_main: bool) -> list[str]:
    rows = [item for item in agents if include_main or item.agent != "main"]
    lines = [
        "start(KST)  agent     label                                     "
        "turns tools   billed       read    peak  min"
    ]
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for item in rows:
        when = item.started_at.astimezone(KST).strftime("%m-%d %H:%M")
        lines.append(
            f"{when}   {item.agent:<8}  {item.label[:40]:<40}  {item.turns:>5} "
            f"{item.tool_calls:>5} {item.billed:>8} {item.cache_read:>10} "
            f"{item.peak_context:>7} {_minutes(item):>4.0f}"
        )
        bucket = totals[item.label]
        bucket[0] += 1
        bucket[1] += item.billed
        bucket[2] += item.cache_read
    if not rows:
        return ["no agent transcripts found"]
    lines.append("")
    lines.append("by label: runs, billed, read")
    for label, (runs, billed, read) in sorted(totals.items()):
        lines.append(f"  {label[:48]:<48} {runs:>3} {billed:>9} {read:>11}")
    lines.append(f"TOTAL_BILLED={sum(item.billed for item in rows)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO-8601 start (default: 24 hours ago)")
    parser.add_argument("--root", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument(
        "--include-main", action="store_true", help="also list the main session transcript"
    )
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    since = _parse(args.since) if args.since else now - timedelta(hours=24)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    agents = collect(transcript_files(args.root), since)
    print("\n".join(render(agents, include_main=args.include_main)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
