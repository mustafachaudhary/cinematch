"""Harvest Letterboxd's all-time popular film ranking, one CSI page at a time.

The visible ``/films/popular/`` page is a JavaScript shell around a server-side
"CSI" fragment.  The fragment is public, paginated, and contains the fields we
need for catalog expansion: rank, slug, title/year, average rating, and the
Letterboxd poster UID.  We deliberately use the fragment instead of a browser
so a 20k-row harvest is deterministic and resumable.

Usage:
    cd backend && ../.venv/bin/python scripts/scrape_letterboxd_popular.py

Outputs (under ``letterboxd_data/top_logged/`` by default):
    pages/page_0001.json        cached parsed rows for one 72-film page
    top_20000.jsonl             deduplicated, rank-ordered rows
    state.json                  progress + challenge/error status

Each page is written atomically.  Re-running skips completed pages, so an
interrupted harvest can be resumed without re-fetching or re-parsing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))

from letterboxd_common import (  # noqa: E402
    BASE,
    ChallengeError,
    fetch,
    is_challenge,
    response_html,
    response_status,
    write_json,
)

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "letterboxd_data" / "top_logged"
CSI_URL = (
    BASE
    + "/csi/films/films-browser-list/popular/page/{page}/?esiAllowFilters=true"
)
TITLE_YEAR_RE = re.compile(r"^(.*?)\s+\((\d{4})\)$")
UID_RE = re.compile(r"^film:(\d+)$")
# Letterboxd's filter cookie is a space-separated ``value-category`` list
# (see the browser bundle's filmFilter parser).  The target is movies, not
# the TV series that otherwise appear in the same popularity ranking.
HIDE_TV_COOKIE = {"filmFilter": "hide-tv"}


def parse_ranked_page(html: str, *, page: int, per_page: int = 72) -> list[dict[str, Any]]:
    """Parse one CSI page into rank-ordered film records.

    Letterboxd sometimes repeats a film if the underlying popularity query
    changes while paginating.  We keep the first (highest) occurrence and
    track the original row ordinal so an audit can detect duplicates.
    """
    soup = BeautifulSoup(html, "lxml")
    items = soup.select("li.posteritem, [data-item-slug]")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        slug = (item.get("data-item-slug") or "").strip()
        if not slug or slug in seen:
            continue
        display = (
            item.get("data-item-full-display-name")
            or item.get("data-item-name")
            or ""
        ).strip()
        match = TITLE_YEAR_RE.match(display)
        title = match.group(1).strip() if match else display
        year = int(match.group(2)) if match else None

        uid = None
        raw_identifier = item.get("data-postered-identifier")
        if raw_identifier:
            try:
                decoded = json.loads(raw_identifier)
                uid = decoded.get("uid")
            except (TypeError, ValueError):
                uid = None
        if not uid:
            uid_match = UID_RE.match(item.get("data-uid") or "")
            uid = item.get("data-uid") if uid_match else None

        container = item if item.name == "li" else item.find_parent("li") or item
        avg = item.get("data-average-rating") or container.get("data-average-rating")
        try:
            average_rating = float(avg) if avg not in (None, "") else None
        except (TypeError, ValueError):
            average_rating = None

        ordinal = len(rows)
        rows.append(
            {
                "rank": (page - 1) * per_page + ordinal + 1,
                "page": page,
                "page_ordinal": ordinal,
                "slug": slug,
                "title": title,
                "year": year,
                "display_name": display,
                "average_rating": average_rating,
                "letterboxd_uid": uid,
                "letterboxd_uri": item.get("data-item-link") or f"/film/{slug}/",
            }
        )
        seen.add(slug)
    return rows


def load_page(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = payload.get("films") if isinstance(payload, dict) else payload
    return rows if isinstance(rows, list) else None


def fetch_page(page: int, cookies: dict | None = None) -> list[dict[str, Any]]:
    url = CSI_URL.format(page=page)
    last_error = None
    for attempt in range(1, 4):
        try:
            response = fetch(url, timeout=45, cookies=cookies)
            status = response_status(response)
            html = response_html(response)
            if is_challenge(response):
                raise ChallengeError(f"anti-bot response on page {page} (status {status})")
            if status != 200:
                raise RuntimeError(f"page {page}: HTTP {status}")
            rows = parse_ranked_page(html, page=page)
            if not rows:
                raise RuntimeError(f"page {page}: no film rows in fragment")
            return rows
        except ChallengeError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= 3:
                break
            time.sleep(2.0 * attempt)
    raise RuntimeError(f"page {page} failed after retries: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pages", type=int, default=320)
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=72)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--include-tv",
        action="store_true",
        help="Do not send Letterboxd's hide-tv filter; useful for an audit only.",
    )
    args = parser.parse_args()
    cookies = None if args.include_tv else HIDE_TV_COOKIE

    out_dir = args.out_dir.resolve()
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "state.json"
    rows_path = out_dir / "top_20000.jsonl"

    started = time.monotonic()
    state: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target": args.limit,
        "pages_requested": args.pages,
        "pages_completed": 0,
        "films": 0,
        "status": "running",
        "filter": "include-tv" if args.include_tv else "hide-tv",
    }
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[dict[str, Any]] = []

    try:
        for page in range(args.start_page, args.pages + 1):
            page_path = pages_dir / f"page_{page:04d}.json"
            rows = None if args.refresh else load_page(page_path)
            if rows is None:
                rows = fetch_page(page, cookies=cookies)
                write_json(
                    page_path,
                    {
                        "page": page,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "films": rows,
                    },
                )
                print(f"page {page}: fetched {len(rows)} films", flush=True)
            else:
                print(f"page {page}: cached {len(rows)} films", flush=True)

            for row in rows:
                slug = row.get("slug")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                row = dict(row)
                # Re-number after filtering/deduplication so the stored rank
                # is the rank in the movie-only list, not the raw TV-inclusive
                # page offset.
                row["rank"] = len(all_rows) + 1
                all_rows.append(row)
                if len(all_rows) >= args.limit:
                    break
            state.update(
                {
                    "pages_completed": page - args.start_page + 1,
                    "films": len(all_rows),
                    "last_page": page,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            write_json(state_path, state)
            if len(all_rows) >= args.limit:
                break
            if len(rows) < args.per_page:
                state["status"] = "short_page"
                break
    except ChallengeError as exc:
        state.update(
            {
                "status": "challenge",
                "error": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_json(state_path, state)
        print(f"CHALLENGE: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        state.update(
            {
                "status": "error",
                "error": repr(exc),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_json(state_path, state)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    temp = rows_path.with_suffix(rows_path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, rows_path)

    elapsed = time.monotonic() - started
    state.update(
        {
            "status": "complete" if len(all_rows) >= args.limit else "partial",
            "films": len(all_rows),
            "elapsed_seconds": round(elapsed, 1),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "output": str(rows_path),
            "duplicate_slugs": len(all_rows) - len(seen),
            "errors": errors,
        }
    )
    write_json(state_path, state)
    print(
        json.dumps(
            {
                "status": state["status"],
                "films": len(all_rows),
                "pages": state.get("last_page"),
                "elapsed_seconds": round(elapsed, 1),
                "output": str(rows_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
