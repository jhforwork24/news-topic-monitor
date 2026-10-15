from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ISSUE_HEADING = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$", re.MULTILINE)
BRIEFING_FILENAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


@dataclass(frozen=True)
class PreviousBriefing:
    report_date: str
    issue_titles: tuple[str, ...]


def load_previous_briefing(root: Path, report_date: str) -> PreviousBriefing | None:
    """Return the issue titles of the latest published briefing before ``report_date``.

    The queue lists them so the editor and the auditor can see which issues the last
    briefing already carried instead of guessing: a follow-up article on an issue that
    was just published is a judgement call the editor makes, and the audit can then
    verify the stated premise from the queue itself. Only the titles the briefing
    published are read; no article text is involved.
    """

    directory = root / "reports" / "briefings"
    try:
        current = date.fromisoformat(report_date)
        names = sorted(path.name for path in directory.iterdir())
    except (OSError, ValueError):
        return None
    for name in reversed(names):
        match = BRIEFING_FILENAME.match(name)
        if not match:
            continue
        try:
            if date.fromisoformat(match.group(1)) >= current:
                continue
        except ValueError:
            continue
        try:
            text = (directory / name).read_text(encoding="utf-8")
        except OSError:
            return None
        titles = tuple(ISSUE_HEADING.findall(text))
        if titles:
            return PreviousBriefing(report_date=match.group(1), issue_titles=titles)
    return None
