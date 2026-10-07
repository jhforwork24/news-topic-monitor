from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .policy import PolicyConfigurationError


def load_skipped_publication_dates(path: Path) -> frozenset[date]:
    """Read the dates on which the user decided to skip publication.

    A missing or malformed calendar is a configuration error, not "no holidays": silently
    treating it as empty would publish on a day the user chose to skip and would shrink the
    next report window.
    """

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyConfigurationError(f"publication calendar unreadable: {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise PolicyConfigurationError(f"publication calendar must be a version 1 mapping: {path}")
    entries = raw.get("skipped_publication_dates")
    if not isinstance(entries, list):
        raise PolicyConfigurationError("skipped_publication_dates must be a list")
    skipped: set[date] = set()
    for entry in entries:
        value = entry.get("date") if isinstance(entry, dict) else None
        if not isinstance(value, date):
            raise PolicyConfigurationError(f"skipped publication entry needs a date: {entry!r}")
        skipped.add(value)
    return frozenset(skipped)
