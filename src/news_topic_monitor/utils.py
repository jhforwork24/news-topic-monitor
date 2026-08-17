from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .constants import KST_NAME, MAX_ERROR_CHARS, MAX_SUMMARY_CHARS

KST = ZoneInfo(KST_NAME)
TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ocid",
    "ref",
    "source",
    "spm",
}
TRACKING_PREFIXES = ("utm_",)


def normalize_url(url: str) -> str:
    raw = html.unescape(url.strip())
    # Some official feeds emit a tracking query as ``&ref=`` without a preceding
    # question mark after double entity escaping. Repair only known tracking keys.
    if "?" not in raw:
        raw = re.sub(
            r"&((?:utm_[^=]+)|ref|source)=",
            r"?\1=",
            raw,
            count=1,
            flags=re.IGNORECASE,
        )
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"invalid HTTP(S) URL: {url!r}")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    clean_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower in TRACKING_KEYS or any(lower.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        clean_query.append((key, value))
    clean_query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(clean_query, doseq=True), ""))


def stable_article_key(
    source: str,
    canonical_url: str | None,
    article_id: str | None,
    title: str,
    published_at: datetime | None,
) -> str:
    if canonical_url:
        basis = f"url:{normalize_url(canonical_url)}"
    elif article_id:
        basis = f"id:{source}:{article_id}"
    else:
        published = published_at.astimezone(UTC).isoformat() if published_at else "unknown"
        basis = f"fallback:{source}:{title.strip()}:{published}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def parse_datetime(value: str | datetime | None, *, now: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    utc_value = parsed.astimezone(UTC)
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    if utc_value < datetime(1990, 1, 1, tzinfo=UTC):
        raise ValueError(f"abnormally old datetime: {value!r}")
    if utc_value > reference + timedelta(hours=6):
        raise ValueError(f"future datetime: {value!r}")
    return utc_value


def in_window(value: datetime | None, start: datetime, end: datetime) -> bool:
    if value is None:
        return True
    return start <= value < end


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def kst_display(value: datetime | None) -> str:
    if value is None:
        return "시각 미상"
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def kst_date(value: datetime) -> str:
    return value.astimezone(KST).date().isoformat()


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    decoded = html.unescape(value)
    if "<" in decoded or ">" in decoded:
        decoded = BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", decoded).strip()


def short_text(value: str | None, limit: int = MAX_SUMMARY_CHARS) -> str | None:
    cleaned = normalize_text(value)
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def short_error(value: str | Exception | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned[:MAX_ERROR_CHARS]


def content_hash(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def unique_preserving_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
