#!/usr/bin/env python3
"""일간 브리핑의 1차 검토 풀을 briefing-archive 형식으로 내보낸다(표준 라이브러리만 사용).

입력: reports/<날짜>.md(일일보고: 조사기간), reports/briefings/<날짜>.md(최종 브리핑),
      data/articles/*.jsonl(원장). 원장 중 조사기간 안에서 자동 판별이 relevant·review인 기사와
      브리핑에 실린 기사를 1차 검토 풀로 본다.
출력: ARCHIVE_DIR 안에 daily/<날짜>.articles.jsonl, daily/<날짜>.clusters.json,
      issues/<날짜>.daily.jsonl 을 추가한다. 같은 경로에 같은 내용이 있으면 건너뛰고,
      다른 내용이면 덮어쓰지 않고 .rev-<UTC시각> 개정본을 만든다(보관소는 추가만 한다).

사용: python scripts/archive_export.py --date 2026-10-24 --archive archive
      (--date 생략 시 reports/briefings/ 의 가장 최근 날짜)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
LABELS = {
    "beminor": "비마이너",
    "ablenews": "에이블뉴스",
    "theindigo": "더인디고",
    "labortoday": "매일노동뉴스",
    "newscham": "참세상",
    "chosun": "조선일보",
    "joongang": "중앙일보",
    "donga": "동아일보",
    "khan": "경향신문",
    "hani": "한겨레",
    "ohmynews": "오마이뉴스",
    "pressian": "프레시안",
    "sisain": "시사IN",
    "mediaus": "미디어스",
}
SECTION = {
    "I": re.compile(r"^# I\. "),
    "II": re.compile(r"^# II\. "),
    "III": re.compile(r"^# III\. "),
}
CLUSTER = re.compile(r"^## (\d+)\. (.+)$")
LINK = re.compile(r"\]\((https?://[^)\s]+)\)\s*$")
WINDOW = re.compile(
    r"조사기간:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 이상,\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 미만"
)


def norm(url: str) -> str:
    url = url.strip().split("#", 1)[0]
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if "?" not in url and url.count("/") > 3 and url.endswith("/"):
        url = url.rstrip("/")
    return url


def kst(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=KST)).astimezone(KST)


def parse_briefing(path: Path) -> list[dict]:
    clusters, section, current = [], None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        for name, pat in SECTION.items():
            if pat.match(line):
                section, current = name, None
        m = CLUSTER.match(line)
        if m and section:
            current = {
                "section": section,
                "number": int(m.group(1)),
                "title": m.group(2).strip(),
                "block": None,
                "issue_id": None,
                "urls": [],
            }
            clusters.append(current)
            continue
        if current and line.startswith("- "):
            link = LINK.search(line)
            if link:
                current["urls"].append(norm(link.group(1)))
    return clusters


def put(archive: Path, rel: str, text: str) -> None:
    dest = archive / rel
    if dest.exists():
        if dest.read_text(encoding="utf-8") == text:
            print(f"동일해서 건너뜀: {rel}")
            return
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = dest.with_name(f"{dest.stem}.rev-{stamp}{dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"추가: {dest.relative_to(archive)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--archive", type=Path, required=True)
    args = ap.parse_args(argv)
    day = args.date or sorted(p.stem for p in Path("reports/briefings").glob("20*.md"))[-1]
    briefing, report = Path(f"reports/briefings/{day}.md"), Path(f"reports/{day}.md")
    if not briefing.exists() or not report.exists():
        print(f"{day}: 최종 브리핑 또는 일일보고가 없어 내보내지 않는다")
        return 0
    w = WINDOW.search(report.read_text(encoding="utf-8"))
    if not w:
        print(f"::error::{report}에서 조사기간을 찾지 못했다")
        return 1
    start = datetime.strptime(w.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    end = datetime.strptime(w.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    clusters = parse_briefing(briefing)
    cited = {u: c for c in clusters for u in c["urls"]}

    ledger: dict[str, dict] = {}
    for p in sorted(Path("data/articles").glob("20*.jsonl")):
        if (
            p.stem < (start - timedelta(days=3)).date().isoformat()
            or p.stem > (end + timedelta(days=1)).date().isoformat()
        ):
            continue
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            u = norm(r.get("canonical_url") or "")
            t = kst(r.get("published_at")) or kst(r.get("first_seen_at"))
            in_window = t is not None and start <= t < end
            if u in cited or (in_window and r.get("classification") in ("relevant", "review")):
                ledger[u] = r

    rows = []
    for u in sorted(set(ledger) | set(cited)):
        r = ledger.get(u, {})
        c = cited.get(u)
        t = kst(r.get("published_at"))
        rows.append(
            {
                "schema": "briefing-archive/articles/v1",
                "pipeline": "daily",
                "issue_date": day,
                "window_start_kst": start.isoformat(),
                "window_end_kst": end.isoformat(),
                "url": u,
                "source": r.get("source"),
                "media": LABELS.get(r.get("source"), r.get("source")),
                "title": r.get("title"),
                "section": r.get("section"),
                "summary": (r.get("summary") or "")[:300] or None,
                "published_kst": t.isoformat() if t else None,
                "published_date_kst": t.date().isoformat() if t else None,
                "screen": {
                    "classification": r.get("classification"),
                    "topic_score": r.get("topic_score"),
                    "matched_terms": r.get("matched_terms"),
                    "found_by": r.get("discovery_route"),
                    "in_ledger": bool(r),
                },
                "review": (
                    {
                        "status": "cited",
                        "section": c["section"],
                        "cluster_no": c["number"],
                        "cluster_title": c["title"],
                        "block": None,
                        "issue_id": None,
                        "reason": None,
                    }
                    if c
                    else {
                        "status": "not_selected",
                        "section": None,
                        "cluster_no": None,
                        "cluster_title": None,
                        "block": None,
                        "issue_id": None,
                        "reason": None,
                    }
                ),
                "body_sha256": r.get("content_hash"),
            }
        )
    issues = [
        {
            "schema": "briefing-archive/issues/v1",
            "issue_id": None,
            "name": c["title"],
            "pipeline": "daily",
            "issue_date": day,
            "section": c["section"],
            "cluster_no": c["number"],
            "block": None,
            "article_count": len(c["urls"]),
            "media": sorted({LABELS.get(ledger.get(u, {}).get("source"), "?") for u in c["urls"]}),
        }
        for c in clusters
    ]
    dump = lambda rs: "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rs)  # noqa: E731
    put(args.archive, f"daily/{day}.articles.jsonl", dump(rows))
    put(
        args.archive,
        f"daily/{day}.clusters.json",
        json.dumps(
            {
                "schema": "briefing-archive/clusters/v1",
                "pipeline": "daily",
                "issue_date": day,
                "window": {"start_kst": start.isoformat(), "end_kst": end.isoformat()},
                "clusters": clusters,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    put(args.archive, f"issues/{day}.daily.jsonl", dump(issues))
    print(
        json.dumps(
            {"date": day, "articles": len(rows), "cited": len(cited), "clusters": len(clusters)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
