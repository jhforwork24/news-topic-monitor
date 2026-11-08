"""Model-free queue prescreen, currently shadow-only.

The editorial queue sends every verified candidate to the connected Claude
editor. A large share of them are mainstream-outlet stories that the rules
already call irrelevant to both I절 and II절 and that carry no hint at all. This
module decides, per queue candidate, whether such a story *would* be removed
from the model's reading by a conservative prescreen.

Shadow mode only records the decision. It never changes the queue, its
``queue_id``, the candidate order or any evidence text, and nothing here may be
used as a reason to exclude, select or describe an article. The decisions are
compared after publication with what the editor actually selected
(``scripts/prescreen_shadow_compare.py``) before any apply mode is considered.

Design rules (2026-11-08):

- Exclusion only. There is no automatic inclusion.
- Only mainstream comparison outlets can be prescreened. Disability press,
  labor press and designated column outlets always stay with the model.
- The candidate must already be ``읽기=앞부분`` in the queue (both rule topics
  irrelevant, no fixed-column or personnel hint), have a non-positive rule
  score, a verified body and an exact publication time, and carry no non-news
  opinion hint.
- Title, summary and the whole queued evidence text must contain none of the
  broad recall clues. The clue set is the union of a static broad list, every
  positive term of both ``config/topics.yml`` topics and the II절 care/poverty/
  minority subsection terms, so a new topic or subsection term widens the clue
  set automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .classifier import RuleClassifier
from .labor_subsections import (
    CARE_SUBSECTION_TERMS,
    MINORITY_SUBSECTION_TERMS,
    POVERTY_SUBSECTION_TERMS,
)
from .models import BodyStatus, Classification, EditorialCandidate, VerificationStatus
from .sources import PRIMARY_COMPARISON_SOURCES
from .storage import JsonlStorage

PRESCREEN_RULE_VERSION = 1
PRESCREEN_SCHEMA_VERSION = 1
PRESCREEN_MODE_ENV = "QUEUE_PRESCREEN_MODE"
PRESCREEN_MODE_OFF = "off"
PRESCREEN_MODE_SHADOW = "shadow"
# Apply mode is deliberately absent: it needs a separate user decision after the
# shadow comparison. Any other value falls back to shadow and is recorded.
SUPPORTED_PRESCREEN_MODES = (PRESCREEN_MODE_OFF, PRESCREEN_MODE_SHADOW)
DEFAULT_PRESCREEN_MODE = PRESCREEN_MODE_SHADOW
MIN_PRESCREEN_EVIDENCE_CHARS = 80

# Positive term groups read from config/topics.yml. excluded_terms (negative
# context such as 서버 장애) and combinations are not recall clues.
TOPIC_CLUE_GROUPS = (
    "core_terms",
    "supporting_terms",
    "laws",
    "policies",
    "organizations",
    "people_aliases",
    "conventions",
)

# Broad recall terms, intentionally wider than the editorial scoring terms.
# Started from the private trial's screening list (rule version 1) and widened
# with the minority-subsection vocabulary the trial list lacked.
BROAD_RECALL_CLUES = (
    "장애",
    "휠체어",
    "자립생활",
    "복지",
    "이동권",
    "탈시설",
    "활동지원",
    "인권",
    "노동",
    "임금",
    "해고",
    "고용",
    "일자리",
    "근로",
    "산재",
    "산업안전",
    "산업재해",
    "파업",
    "노조",
    "노사",
    "교섭",
    "단식",
    "농성",
    "빈곤",
    "생활비",
    "주거",
    "연금",
    "공공기관",
    "사회보장",
    "보험",
    "비정규",
    "프리랜서",
    "택시",
    "이주",
    "안전",
    "아리셀",
    "청소",
    "최저",
    "돌봄",
    "노역",
    "수용자",
    "차별",
    "권리",
    "생계",
    "소득",
    "체불",
    "중대재해",
    "기초생활",
    "민주노총",
    "한국노총",
    "요양보호",
    "재해",
    "하청",
    "직장",
    "일터",
    "노인",
    "수급",
    "퇴직",
    "휴직",
    "간병",
    "소수자",
    "난민",
    "성소수자",
    "트랜스젠더",
    "퀴어",
    "혐오",
    "성평등",
    "여성",
    "disability",
    "disabled",
    "labor",
    "labour",
    "worker",
    "wage",
    "union",
    "strike",
    "care",
    "welfare",
    "poverty",
    "housing",
    "migrant",
    "refugee",
)


@dataclass(frozen=True)
class PrescreenDecision:
    candidate_id: str
    source: str
    would_exclude: bool
    reason: str


def prescreen_mode(environ: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Return the effective mode and, for an unsupported value, a recorded note."""

    raw = (environ if environ is not None else os.environ).get(PRESCREEN_MODE_ENV, "")
    value = raw.strip().lower()
    if not value:
        return DEFAULT_PRESCREEN_MODE, None
    if value in SUPPORTED_PRESCREEN_MODES:
        return value, None
    return DEFAULT_PRESCREEN_MODE, f"unsupported_mode:{value[:20]}"


def recall_clues(topics_path: Path) -> tuple[str, ...]:
    """Union of the broad list, positive topic terms and subsection terms."""

    config = yaml.safe_load(topics_path.read_text(encoding="utf-8")) or {}
    terms: set[str] = {term.casefold() for term in BROAD_RECALL_CLUES}
    for topic in (config.get("topics") or {}).values():
        for group in TOPIC_CLUE_GROUPS:
            for entry in topic.get(group) or ():
                if not isinstance(entry, dict):
                    continue
                term = str(entry.get("term", "")).strip()
                score = float(entry.get("score", 0) or 0)
                if term and score > 0:
                    terms.add(term.casefold())
    for term in (*CARE_SUBSECTION_TERMS, *POVERTY_SUBSECTION_TERMS, *MINORITY_SUBSECTION_TERMS):
        terms.add(term.casefold())
    return tuple(sorted(terms))


def clue_set_sha256(clues: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(clues).encode("utf-8")).hexdigest()


def decide(
    candidate: EditorialCandidate,
    *,
    labor_classifier: RuleClassifier,
    clues: tuple[str, ...],
) -> PrescreenDecision:
    """Decide one already-bounded queue candidate. Uncertain means keep."""

    # Imported here: notion_publish imports a large part of the package and the
    # queue reading tier is defined there as the single source of truth.
    from .notion_publish import (
        READING_TIER_LEAD,
        _non_news_opinion_hint,
        queue_reading_tier,
    )

    def keep(reason: str) -> PrescreenDecision:
        return PrescreenDecision(candidate.candidate_id, candidate.source, False, reason)

    if candidate.source not in PRIMARY_COMPARISON_SOURCES:
        return keep("keep:designated_or_specialist_source")
    if candidate.rule_classification != Classification.IRRELEVANT or candidate.rule_score > 0:
        return keep("keep:rule_signal")
    if (
        candidate.body_status != BodyStatus.FETCHED
        or candidate.verification_status != VerificationStatus.BODY_VERIFIED
        or candidate.published_at is None
        or len(candidate.evidence_text.strip()) < MIN_PRESCREEN_EVIDENCE_CHARS
    ):
        return keep("keep:evidence_not_verified")
    if queue_reading_tier(candidate, labor_classifier) != READING_TIER_LEAD:
        return keep("keep:full_reading_tier")
    if _non_news_opinion_hint(candidate):
        return keep("keep:opinion_hint")
    fields = (candidate.title, candidate.summary, candidate.section, candidate.evidence_text)
    text = " ".join(value for value in fields if value).casefold()
    if any(clue in text for clue in clues):
        return keep("keep:recall_clue")
    return PrescreenDecision(
        candidate.candidate_id, candidate.source, True, "would_exclude:no_signal"
    )


def shadow_record(
    bounded: list[EditorialCandidate],
    *,
    report_date: str,
    queue_id: str,
    labor_classifier: RuleClassifier,
    topics_path: Path,
    mode_note: str | None = None,
) -> dict[str, Any]:
    """Build the shadow record. Contains IDs, sources and counts only."""

    clues = recall_clues(topics_path)
    decisions = [
        decide(candidate, labor_classifier=labor_classifier, clues=clues) for candidate in bounded
    ]
    excluded = [decision for decision in decisions if decision.would_exclude]
    return {
        "schema_version": PRESCREEN_SCHEMA_VERSION,
        "rule_version": PRESCREEN_RULE_VERSION,
        "mode": PRESCREEN_MODE_SHADOW,
        "applied": False,
        "mode_note": mode_note,
        "report_date": report_date,
        "queue_id": queue_id,
        "generated_at": datetime.now(UTC),
        "candidate_count": len(bounded),
        "would_exclude_count": len(excluded),
        "keep_reason_counts": dict(
            sorted(Counter(d.reason for d in decisions if not d.would_exclude).items())
        ),
        "would_exclude_by_source": dict(sorted(Counter(d.source for d in excluded).items())),
        "clue_count": len(clues),
        "clue_set_sha256": clue_set_sha256(clues),
        "would_exclude": [
            {"candidate_id": decision.candidate_id, "source": decision.source}
            for decision in excluded
        ],
    }


def shadow_record_path(root: Path, report_date: str) -> Path:
    return root / "health" / "editorial_queue" / "prescreen_shadow" / f"{report_date}.json"


def shadow_compare_path(root: Path, report_date: str) -> Path:
    return (
        root / "health" / "editorial_queue" / "prescreen_shadow" / "compare" / f"{report_date}.json"
    )


def write_shadow_record(root: Path, record: dict[str, Any]) -> Path:
    path = shadow_record_path(root, record["report_date"])
    JsonlStorage.atomic_write_json(path, record)
    return path


def compare_shadow(root: Path, report_date: str) -> dict[str, Any]:
    """Compare a day's shadow decisions with the published selection.

    ``evidence/DATE.json`` binds each published article to an ``issue_id``. A
    would-exclude candidate that the editor actually published is a recall miss
    and blocks any move towards apply mode.
    """

    result: dict[str, Any] = {
        "schema_version": PRESCREEN_SCHEMA_VERSION,
        "report_date": report_date,
        "checked_at": datetime.now(UTC),
        "status": None,
        "queue_id": None,
        "would_exclude_count": None,
        "published_candidate_count": None,
        "missing_from_evidence_count": None,
        "overlap": [],
    }
    shadow_path = shadow_record_path(root, report_date)
    if not shadow_path.exists():
        result["status"] = "no_shadow_record"
        return result
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    result["queue_id"] = shadow.get("queue_id")
    result["would_exclude_count"] = shadow.get("would_exclude_count")

    bound_path = root / "health" / "editorial_queue" / "initial_health" / f"{report_date}.json"
    if bound_path.exists():
        bound_queue_id = json.loads(bound_path.read_text(encoding="utf-8")).get("queue_id")
        if bound_queue_id and bound_queue_id != shadow.get("queue_id"):
            result["status"] = "queue_mismatch"
            return result

    evidence_path = root / "evidence" / f"{report_date}.json"
    if not evidence_path.exists():
        result["status"] = "no_published_evidence"
        return result
    articles = {
        article["candidate_id"]: article
        for article in json.loads(evidence_path.read_text(encoding="utf-8")).get("articles", [])
        if article.get("candidate_id")
    }
    published = {cid for cid, article in articles.items() if article.get("issue_id")}
    excluded_ids = [item["candidate_id"] for item in shadow.get("would_exclude", [])]
    result["published_candidate_count"] = len(published)
    result["missing_from_evidence_count"] = sum(1 for cid in excluded_ids if cid not in articles)
    result["overlap"] = [
        {
            "candidate_id": cid,
            "source": articles[cid].get("outlet"),
            "issue_id": articles[cid].get("issue_id"),
            "title": articles[cid].get("title"),
        }
        for cid in excluded_ids
        if cid in published
    ]
    result["status"] = "recall_miss" if result["overlap"] else "no_overlap"
    return result
