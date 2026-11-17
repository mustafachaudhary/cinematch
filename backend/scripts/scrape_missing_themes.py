"""Incrementally scrape Letterboxd theme pages for films missing from the dataset.

The June 2024 Kaggle snapshot covers older catalog films.  New releases and
late-2024/2025/2026 titles are fetched from ``/film/{slug}/themes/``.  Results
are appended to a JSONL checkpoint so a challenge or interruption is cheap to
resume.

Usage:
    cd backend && ../.venv/bin/python scripts/scrape_missing_themes.py \
        --limit 20 --workers 2 --interval-min 1.2 --interval-max 1.8
    ... --workers 3 --interval-min 0.6 --interval-max 0.9   # faster, monitor 403s
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from letterboxd_common import (  # noqa: E402
    BASE,
    fetch,
    is_challenge,
    response_html,
    response_status,
)

THEME_LINK_RE = re.compile(r"^/films/(theme|mini-theme)/([^/]+)/")


def normalize_theme(href: str, label: str) -> dict | None:
    match = THEME_LINK_RE.match(href or "")
    if not match:
        return None
    kind, slug = match.group(1), match.group(2)
    label = (label or "").strip()
    if not label:
        return None
    return {
        "label": label,
        "kind": kind,
        "slug": slug,
        "source": "letterboxd_page",
    }


def fetch_themes(row: dict, interval_min: float, interval_max: float) -> dict:
    slug = row.get("slug")
    if not slug:
        return {**row, "status": "missing_slug", "themes": []}
    try:
        response = fetch(
            f"{BASE}/film/{slug}/themes/",
            timeout=45,
            min_seconds=interval_min,
            max_seconds=interval_max,
        )
    except Exception as exc:  # noqa: BLE001
        return {**row, "status": f"error:{type(exc).__name__}", "themes": []}
    status = response_status(response)
    if is_challenge(response):
        return {**row, "status": "challenge", "http_status": status, "themes": []}
    if status != 200:
        return {**row, "status": f"http_{status}", "http_status": status, "themes": []}

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response_html(response), "lxml")
    themes = []
    seen = set()
    for anchor in soup.select('a[href*="/films/theme/"], a[href*="/films/mini-theme/"]'):
        item = normalize_theme(anchor.get("href") or "", anchor.get_text(" ", strip=True))
        if not item:
            continue
        key = (item["kind"], item["slug"])
        if key in seen:
            continue
        seen.add(key)
        themes.append(item)
    return {
        **row,
        "status": "ok" if themes else "no_themes",
        "http_status": status,
        "themes": themes,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--missing",
        type=Path,
        default=BACKEND / "letterboxd_data" / "top_logged" / "themes_missing.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=BACKEND / "letterboxd_data" / "top_logged" / "themes_scraped.jsonl",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--interval-min", type=float, default=0.6)
    parser.add_argument("--interval-max", type=float, default=0.9)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    missing = [
        json.loads(line)
        for line in args.missing.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    done = set()
    if args.out.exists() and not args.refresh:
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            # Transient DNS/connection failures and challenges are retried on
            # the next run; a terminal page result should never be fetched again.
            if row.get("tmdb_id") and row.get("status") in {
                "ok",
                "no_themes",
                "missing_slug",
                "http_404",
            }:
                done.add(int(row["tmdb_id"]))
    pending = [row for row in missing if int(row["tmdb_id"]) not in done]
    if args.limit:
        pending = pending[: args.limit]

    print(
        json.dumps(
            {
                "missing": len(missing),
                "already_done": len(done),
                "pending": len(pending),
                "workers": args.workers,
                "interval": [args.interval_min, args.interval_max],
            },
            indent=2,
        ),
        flush=True,
    )
    if not pending:
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stats: dict[str, int] = {}
    challenge = threading.Event()
    with args.out.open("a", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {}
            for row in pending:
                if challenge.is_set():
                    break
                futures[pool.submit(fetch_themes, row, args.interval_min, args.interval_max)] = row
            for index, future in enumerate(as_completed(futures), 1):
                result = future.result()
                status = result.get("status", "unknown")
                stats[status] = stats.get(status, 0) + 1
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                if status == "challenge":
                    challenge.set()
                if index % 25 == 0 or index == len(futures):
                    elapsed = max(0.001, time.monotonic() - started)
                    print(
                        f"  themes {index}/{len(futures)} "
                        f"rate={index / elapsed:.2f}/s stats={stats}",
                        flush=True,
                    )
    print(json.dumps({"stats": stats, "challenge": challenge.is_set()}, indent=2))
    return 1 if challenge.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
