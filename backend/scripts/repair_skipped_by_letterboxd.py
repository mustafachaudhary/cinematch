"""Repair skipped top-list films with the exact TMDB id on Letterboxd.

A title/year mismatch usually means TMDB search chose a different film, not
that the Letterboxd entry is invalid.  For those rows (and only those), read
the film page's authoritative ``data-tmdb-id``, update ``resolution.db``, then
reuse the idempotent importer on a tiny filtered resolution database.

Usage:
    cd backend && ../.venv/bin/python scripts/repair_skipped_by_letterboxd.py --dry-run
    ... --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TOP = BACKEND / "letterboxd_data" / "top_logged"
sys.path.insert(0, str(BACKEND / "scripts"))

from resolve_top_catalog import fetch_letterboxd_film  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=BACKEND / "cinematch.db")
    parser.add_argument("--resolution-db", type=Path, default=TOP / "resolution.db")
    parser.add_argument("--temp", type=Path, default=Path("/tmp/opencode/repair_skipped_resolution.db"))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--rps", type=float, default=6.0)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in con.execute(
            "select rank, slug, title, year, tmdb_id, notes from letterboxd_popularity "
            "where status = 'skipped' and notes != 'tmdb_movie_not_found' order by rank"
        )
    ]
    con.close()
    print(json.dumps({"targets": len(rows), "apply": args.apply}, indent=2), flush=True)

    changes = []
    failed = []
    for index, row in enumerate(rows, 1):
        try:
            page = fetch_letterboxd_film(row["slug"])
        except Exception as exc:  # noqa: BLE001
            failed.append({"slug": row["slug"], "error": repr(exc)[:300]})
            page = None
        if not page or not page.get("tmdb_id"):
            failed.append({"slug": row["slug"], "error": "no_tmdb_id"})
        else:
            changes.append(
                {
                    "slug": row["slug"],
                    "old_tmdb_id": row.get("tmdb_id"),
                    "new_tmdb_id": page["tmdb_id"],
                    "page_title": page.get("title"),
                    "page_year": page.get("year"),
                    "rank": row["rank"],
                }
            )
        if index % 10 == 0 or index == len(rows):
            print(f"  letterboxd {index}/{len(rows)} resolved={len(changes)} failed={len(failed)}", flush=True)
        time.sleep(max(0.0, args.delay))

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "targets": len(rows),
        "changes": changes,
        "failed": failed,
        "applied": args.apply,
    }
    out = BACKEND / "reports" / f"skipped_letterboxd_repair_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report: {out}")
    if not args.apply or not changes:
        con.close()
        return 0 if not failed else 1

    resolution = sqlite3.connect(args.resolution_db)
    now = datetime.now(timezone.utc).isoformat()
    for change in changes:
        page_payload = {
            "tmdb_id": change["new_tmdb_id"],
            "title": change["page_title"],
            "year": change["page_year"],
            "slug": change["slug"],
            "source": "letterboxd_film_page_skipped_repair",
        }
        resolution.execute(
            "update resolution set tmdb_id = ?, status = 'resolved_letterboxd_repair_skipped', "
            "method = 'letterboxd_repair_skipped', reason = null, payload = ?, updated_at = ? "
            "where slug = ?",
            (
                change["new_tmdb_id"],
                json.dumps(page_payload, ensure_ascii=False),
                now,
                change["slug"],
            ),
        )
    resolution.commit()
    resolution.close()

    con = sqlite3.connect(args.db)
    rank_by_slug = {r[0]: int(r[1]) for r in con.execute("select slug, rank from letterboxd_popularity")}
    con.close()

    args.temp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.resolution_db, args.temp)
    temp = sqlite3.connect(args.temp)
    keep = [change["slug"] for change in changes]
    temp.execute("drop table if exists repair_keep")
    temp.execute("create table repair_keep (slug text primary key)")
    temp.executemany("insert or ignore into repair_keep values (?)", [(slug,) for slug in keep])
    temp.execute("delete from resolution where slug not in (select slug from repair_keep)")
    for slug in keep:
        if slug in rank_by_slug:
            temp.execute("update resolution set rank = ? where slug = ?", (rank_by_slug[slug], slug))
    temp.commit()
    print("temporary rows", temp.execute("select count(*) from resolution").fetchone()[0], flush=True)
    temp.close()

    command = [
        sys.executable,
        str(BACKEND / "scripts" / "import_top_catalog.py"),
        "--db", str(args.db.resolve()),
        "--resolution-db", str(args.temp),
        "--allow-live",
        "--trust-resolution",
        "--workers", str(args.workers),
        "--rps", str(args.rps),
        "--report", str(BACKEND / "reports" / "skipped_letterboxd_repair_import.json"),
    ]
    return subprocess.call(command, cwd=BACKEND)


if __name__ == "__main__":
    raise SystemExit(main())
