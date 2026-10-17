#!/usr/bin/env python3
"""Mechanically check that a Claude editorial draft's figures and titles appear in its evidence.

The editor and the independent auditor both compare the draft's `title`, `summary` and
`tone_analysis` with the selected candidates' `evidence_text` by reading. This script does the
mechanical part of that comparison first, so the model only has to judge the leftovers:

  figure   a number with its unit (`4620만 원`, `53%`, `12일`) the evidence does not contain
  unit     the digits occur in the evidence but never with this unit (`217명` vs `217건`)
  title    a job title (`위원장`, `장관`, ...) none of the issue's evidence uses
  entity   an institution-like word (`...부`, `...위원회`, `...공단`) the evidence does not contain
  quote    a quoted phrase the evidence does not contain
  id       a candidate_id the queue does not have, or an excluded id that is also selected

Findings are hints, not verdicts: a derived figure (`10만 → 12만` written as `2만 명 늘`) or a
paraphrased name is reported too, and a clean run proves nothing about meaning, attribution or
tone. It never decides publication; the audit still reads the selected candidates in full.

Only the draft's own words are printed (at most a short context), never article text, so the
output is safe to paste into an audit note.

Inputs are local files the editor/auditor already saved: the draft (the JSON itself or a saved
Notion fetch of the draft page) and the queue parts (JSON, or saved Notion fetches of the part
pages). Nothing is fetched from the network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
NUMBER = re.compile(
    r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s?(만|억|조|천|백)?\s?([명원%곳건개일월년차회호배세대위점표쪽시분주]?)"
)
HANGUL_WORD = re.compile(r"[가-힣]+")
QUOTED = re.compile("[\u201c\"\u2018']([^\u201c\u201d\"\u2018\u2019']{4,60})[\u201d\"\u2019']")
TITLES = (
    "대통령",
    "총리",
    "장관",
    "차관",
    "위원장",
    "부위원장",
    "회장",
    "부회장",
    "이사장",
    "원장",
    "사장",
    "대표",
    "국장",
    "과장",
    "본부장",
    "센터장",
    "총장",
    "청장",
    "처장",
    "소장",
    "시장",
    "구청장",
    "군수",
    "도지사",
    "교육감",
    "의원",
    "교수",
    "변호사",
)
ENTITY_SUFFIXES = (
    "위원회",
    "공사",
    "공단",
    "센터",
    "병원",
    "협회",
    "연대",
    "노조",
    "본부",
    "재단",
    "대학교",
    "학교",
    "연맹",
    "공동대책위원회",
    "공동투쟁단",
    "행정부",
    "복지부",
    "노동부",
    "교육부",
    "국토부",
    "부처",
    "교통공사",
    "진흥원",
    "연구원",
    "기구",
)
PARTICLES = (
    "으로",
    "에서",
    "까지",
    "부터",
    "에게",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "도",
    "와",
    "과",
    "로",
)
SECTION_FIELDS = ("title", "summary", "tone_analysis")


@dataclass(frozen=True)
class Finding:
    issue: int
    title: str
    kind: str
    text: str
    field: str

    def render(self) -> str:
        label = self.title if len(self.title) <= 24 else self.title[:24] + "…"
        return f"이슈 {self.issue} [{label}] {self.kind}: {self.text} ({self.field})"


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = text.translate(
        str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})
    )
    return re.sub(r"\s+", "", text)


def _json_objects(text: str) -> list[Any]:
    """Return every JSON value found in ``text``: the whole text, a fetch wrapper's text, fences."""

    found: list[Any] = []
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if whole is not None:
        found.append(whole)
        if isinstance(whole, dict) and isinstance(whole.get("text"), str):
            text = whole["text"]
    for match in JSON_FENCE.finditer(text):
        try:
            found.append(json.loads(match.group(1)))
        except ValueError:
            continue
    return found


def load_draft(path: Path) -> dict[str, Any]:
    for item in _json_objects(path.read_text(encoding="utf-8")):
        if isinstance(item, dict) and isinstance(item.get("plan"), dict):
            return item
    raise SystemExit(f"{path}: no draft JSON with a 'plan' found")


def load_candidate_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Every candidate row found in the given files, de-duplicated by candidate_id."""

    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        for item in _json_objects(path.read_text(encoding="utf-8")):
            found = item.get("candidates") if isinstance(item, dict) else item
            if not isinstance(found, list):
                continue
            for row in found:
                if isinstance(row, dict) and isinstance(row.get("candidate_id"), str):
                    rows[row["candidate_id"]] = row
    return list(rows.values())


def evidence_index(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    """candidate_id -> normalized evidence text (title and summary included)."""

    evidence: dict[str, str] = {}
    for row in rows:
        parts = (row.get("title"), row.get("summary"), row.get("evidence_text"))
        evidence[row["candidate_id"]] = _normalize(
            " ".join(part for part in parts if isinstance(part, str))
        )
    return evidence


def load_candidates(paths: Iterable[Path]) -> dict[str, str]:
    return evidence_index(load_candidate_rows(paths))


def _strip_particle(word: str) -> str:
    for particle in PARTICLES:
        if word.endswith(particle) and len(word) > len(particle) + 1:
            return word[: -len(particle)]
    return word


def _variants(word: str) -> set[str]:
    return {word, _strip_particle(word)}


def _context(text: str, start: int, end: int) -> str:
    return text[max(0, start - 8) : end + 8].replace("\n", " ")


def _number_findings(text: str, evidence: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in NUMBER.finditer(text):
        digits = match.group(1).replace(",", "")
        scale, unit = match.group(2) or "", match.group(3) or ""
        if not scale and not unit and len(digits) < 3:
            continue
        key = digits + scale + unit
        if key in seen:
            continue
        seen.add(key)
        if key in evidence:
            continue
        bare = re.search(rf"(?<![\d.]){re.escape(digits)}(?![\d])", evidence)
        kind = "unit" if bare and (scale or unit) else "figure"
        if kind == "unit" and digits + scale in evidence and not unit:
            continue
        findings.append((kind, _context(text, match.start(), match.end())))
    return findings


def _title_findings(text: str, evidence: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in HANGUL_WORD.finditer(text):
        word = match.group(0)
        for variant in _variants(word):
            if variant in TITLES and variant not in seen:
                seen.add(variant)
                pattern = rf"(?<![가-힣]){re.escape(variant)}"
                if not re.search(pattern, evidence) and variant not in evidence:
                    findings.append(("title", _context(text, match.start(), match.end())))
    return findings


def _entity_findings(text: str, evidence: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in HANGUL_WORD.finditer(text):
        word = match.group(0)
        stripped = _strip_particle(word)
        if len(stripped) < 3 or not stripped.endswith(ENTITY_SUFFIXES) or stripped in TITLES:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        if not any(variant in evidence for variant in _variants(word)):
            findings.append(("entity", stripped))
    return findings


def _quote_findings(text: str, evidence: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for match in QUOTED.finditer(text):
        phrase = _normalize(match.group(1))
        if phrase and phrase not in evidence:
            findings.append(("quote", match.group(1)))
    return findings


def check_draft(draft: dict[str, Any], candidates: dict[str, str]) -> list[Finding]:
    plan = draft.get("plan") if isinstance(draft.get("plan"), dict) else {}
    issues = plan.get("issues") if isinstance(plan.get("issues"), list) else []
    exclusions = plan.get("exclusions") if isinstance(plan.get("exclusions"), list) else []
    findings: list[Finding] = []
    selected: set[str] = set()
    for number, issue in enumerate(issues, start=1):
        if not isinstance(issue, dict):
            continue
        title = str(issue.get("title") or "")
        ids = [str(item) for item in issue.get("candidate_ids") or []]
        selected.update(ids)
        for missing in (item for item in ids if item not in candidates):
            findings.append(
                Finding(number, title, "id", f"{missing[:12]}… 대기열에 없음", "candidate_ids")
            )
        evidence = "".join(candidates[item] for item in ids if item in candidates)
        if not evidence:
            continue
        for field in SECTION_FIELDS:
            text = str(issue.get(field) or "")
            for check in (_number_findings, _title_findings, _entity_findings, _quote_findings):
                for kind, shown in check(text, evidence):
                    findings.append(Finding(number, title, kind, shown, field))
    for row in exclusions:
        candidate_id = str(row.get("candidate_id")) if isinstance(row, dict) else ""
        if candidate_id and candidate_id not in candidates:
            findings.append(
                Finding(0, "exclusions", "id", f"{candidate_id[:12]}… 대기열에 없음", "exclusions")
            )
        if candidate_id in selected:
            findings.append(
                Finding(0, "exclusions", "id", f"{candidate_id[:12]}… 선정과 중복", "exclusions")
            )
    return findings


def preflight(
    draft: dict[str, Any], rows: list[dict[str, Any]], *, check_queue_id: bool = False
) -> list[str]:
    """The checks finalize applies to a draft, run locally before it is submitted.

    Covers the draft schema, the plan rules (candidate ids, section and subsection limits,
    exclusions, section-eligible outlets), optionally the queue_id digest and the mechanical text
    rules (summary form, tone sentence counts, outlet names, keyword). Nothing here reads meaning.
    """

    root = Path(__file__).resolve().parents[1] / "src"
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pydantic import ValidationError

    from news_topic_monitor.briefing_validation import issue_text_errors
    from news_topic_monitor.chat_bridge import ChatEditorialDraft, editorial_queue_id
    from news_topic_monitor.editorial import EditorialValidationError, validate_external_plan
    from news_topic_monitor.models import EditorialCandidate, EditorialSection

    errors: list[str] = []
    try:
        parsed = ChatEditorialDraft.model_validate(draft)
    except ValidationError as exc:
        return [f"schema: {error['loc']} {error['msg']}" for error in exc.errors()[:10]]
    try:
        candidates = [EditorialCandidate.model_validate(row) for row in rows]
    except ValidationError as exc:
        return [f"candidates: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"]
    if check_queue_id and editorial_queue_id(candidates) != parsed.queue_id:
        errors.append(
            f"queue_id: 입력 후보 {len(candidates)}개의 digest가 초안 queue_id와 다름 "
            "(후보 묶음이 모두 입력되지 않았거나 queue_id가 틀림)"
        )
    try:
        validate_external_plan(parsed.plan, candidates)
    except EditorialValidationError as exc:
        errors.extend(f"plan: {part.strip()}" for part in str(exc).split(": ", 1)[-1].split(";"))
    for number, issue in enumerate(parsed.plan.issues, start=1):
        errors.extend(
            f"text: 이슈 {number} {message.split(': ', 1)[-1]}"
            for message in issue_text_errors(
                label=f"이슈 {number}",
                title=issue.title,
                summary=issue.summary,
                tone_analysis=issue.tone_analysis,
                keyword=issue.keyword,
                article_count=len(issue.candidate_ids),
                opinion_section=issue.section == EditorialSection.OPINION,
            )
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--draft", type=Path, required=True, help="draft JSON or saved Notion fetch"
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        nargs="+",
        required=True,
        help="queue part JSON files or saved Notion fetches of the part pages",
    )
    parser.add_argument("--strict", action="store_true", help="exit 1 when anything is reported")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="also run the finalize-side schema, plan and text-form checks (needs all parts)",
    )
    parser.add_argument(
        "--check-queue-id",
        action="store_true",
        help="with --preflight, also compare the digest of the given candidates with queue_id; "
        "only meaningful when every part was saved byte for byte (finalize checks it anyway)",
    )
    args = parser.parse_args(argv)
    draft = load_draft(args.draft)
    rows = load_candidate_rows(args.candidates)
    candidates = evidence_index(rows)
    if not candidates:
        print("no candidates found in the given files", file=sys.stderr)
        return 2
    findings = check_draft(draft, candidates)
    problems = preflight(draft, rows, check_queue_id=args.check_queue_id) if args.preflight else []
    issues = (draft.get("plan") or {}).get("issues") or []
    print(
        f"candidates={len(candidates)} issues={len(issues)} findings={len(findings)} "
        f"preflight_errors={len(problems)}"
    )
    for problem in problems:
        print(f"PREFLIGHT {problem}")
    for finding in findings:
        print(finding.render())
    if not findings and not problems:
        print("GROUNDING_CLEAN (의미·귀속·논조는 이 검사 대상이 아니다)")
    problems_found = bool(findings or problems)
    return 1 if problems_found and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
