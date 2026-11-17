"""One-time local metadata backfill for catalog rows created before assets.

The expanded import stores the full TMDB payload needed by the detail page in
``movie_cache.assets``.  Older catalog rows predate that column and should not
trigger a runtime TMDB request just to render a backdrop, tagline or trailer.
This script fills only missing values, never overwrites curated catalog fields.

Usage:
    cd backend && ../.venv/bin/python scripts/backfill_movie_assets.py \
        --db cinematch.db --workers 8 --rps 16
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def assets_json(payload: dict) -> str:
    keys = (
        "tagline",
        "status",
        "release_date",
        "backdrop_path",
        "spoken_languages",
        "production_countries",
        "production_companies",
        "budget",
        "revenue",
        "homepage",
        "certification",
        "videos",
        "images",
    )
    assets = {key: payload.get(key) for key in keys if payload.get(key) not in (None, "", [], {})}
    return json.dumps(assets, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=BACKEND / "cinematch.db")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rps", type=float, default=16.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db_path = args.db.resolve()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in con.execute(
            "select tmdb_id, title, release_year from movie_cache "
            "where assets is null or assets = '' order by popularity desc, tmdb_id"
        )
    ]
    if args.limit:
        rows = rows[: args.limit]
    print(json.dumps({"db": str(db_path), "missing_assets": len(rows), "dry_run": args.dry_run}, indent=2))
    if args.dry_run or not rows:
        con.close()
        return 0

    from services import tmdb

    tmdb.configure_rate_limit(args.rps)
    started = time.monotonic()
    ok = 0
    failed = []
    updates = []

    def apply(payload: dict) -> None:
        assets = assets_json(payload)
        keywords = json.dumps(payload.get("keywords") or [], ensure_ascii=False)
        con.execute(
            """
            update movie_cache
               set assets = ?,
                   keywords = coalesce(nullif(keywords, ''), ?),
                   collection_id = coalesce(collection_id, ?),
                   imdb_id = coalesce(imdb_id, ?),
                   runtime = coalesce(runtime, ?),
                   original_language = coalesce(original_language, ?),
                   vote_count = coalesce(vote_count, ?),
                   poster_path = coalesce(poster_path, ?),
                   overview = coalesce(nullif(overview, ''), ?)
             where tmdb_id = ?
            """,
            (
                assets,
                keywords,
                payload.get("collection_id"),
                payload.get("imdb_id"),
                payload.get("runtime"),
                payload.get("original_language"),
                payload.get("vote_count"),
                payload.get("poster_path"),
                payload.get("overview"),
                int(payload["tmdb_id"]),
            ),
        )

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(tmdb.get_movie_full, int(row["tmdb_id"])): row for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                payload = future.result()
            except Exception as exc:  # noqa: BLE001
                failed.append({"tmdb_id": row["tmdb_id"], "error": repr(exc)[:300]})
                payload = None
            if not payload:
                failed.append({"tmdb_id": row["tmdb_id"], "error": "no_payload"})
            else:
                apply(payload)
                ok += 1
                if ok % 100 == 0:
                    con.commit()
            if index % 100 == 0 or index == len(futures):
                elapsed = max(0.001, time.monotonic() - started)
                print(f"  assets {index}/{len(futures)} ok={ok} failed={len(failed)} rate={index/elapsed:.1f}/s", flush=True)
    con.commit()
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "targets": len(rows),
        "updated": ok,
        "failed": failed,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    out = BACKEND / "reports" / f"asset_backfill_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "failed"}, indent=2))
    print(f"failed: {len(failed)} report: {out}")
    con.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
