"""Retry top-list rows the importer marked as missing from TMDB.

DNS/connection hiccups and genuine 404 TV specials both arrive at the importer
as ``tmdb_movie_not_found``.  This script builds a temporary resolution DB
containing only those rows and reruns the idempotent importer, so a transient
outage does not permanently lose a film.  Genuine 404s are harmless: they are
marked skipped again and remain auditable in ``letterboxd_popularity``.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_TOP = BACKEND / "letterboxd_data" / "top_logged"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=BACKEND / "cinematch.db")
    parser.add_argument("--resolution-db", type=Path, default=DEFAULT_TOP / "resolution.db")
    parser.add_argument("--temp", type=Path, default=Path("/tmp/opencode/retry_resolution.db"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rps", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2
    con = sqlite3.connect(db_path)
    ids = [
        int(row[0])
        for row in con.execute(
            "select distinct tmdb_id from letterboxd_popularity "
            "where status = 'skipped' and notes = 'tmdb_movie_not_found' "
            "and tmdb_id is not null"
        )
    ]
    rank_by_slug = {
        row[0]: int(row[1])
        for row in con.execute(
            "select slug, rank from letterboxd_popularity where slug is not null"
        )
    }
    con.close()
    print({"retry_ids": len(ids), "db": str(db_path), "dry_run": args.dry_run})
    if args.dry_run or not ids:
        return 0

    args.temp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.resolution_db, args.temp)
    temp = sqlite3.connect(args.temp)
    temp.execute("drop table if exists retry_keep")
    temp.execute("create table retry_keep (tmdb_id integer primary key)")
    temp.executemany("insert or ignore into retry_keep values (?)", [(tmdb_id,) for tmdb_id in ids])
    temp.execute(
        "delete from resolution where tmdb_id is null "
        "or tmdb_id not in (select tmdb_id from retry_keep)"
    )
    # ``resolution.db`` is ordered by the unfiltered harvest rank, while the
    # exposed popularity table is ordered by a separate TV-filtered harvest.
    # Align the retry rows to the popularity table before the idempotent
    # importer upserts them; otherwise two different source ranks can point at
    # the same unique slug and abort the whole retry.
    for slug, rank in rank_by_slug.items():
        temp.execute("update resolution set rank = ? where slug = ?", (rank, slug))
    temp.commit()
    remaining = temp.execute("select count(*) from resolution").fetchone()[0]
    temp.commit()
    temp.close()
    print({"temporary_rows": remaining, "temp": str(args.temp)})

    command = [
        sys.executable,
        str(BACKEND / "scripts" / "import_top_catalog.py"),
        "--db", str(db_path),
        "--resolution-db", str(args.temp),
        "--allow-live",
        "--workers", str(args.workers),
        "--rps", str(args.rps),
        "--report", str(BACKEND / "reports" / "top_catalog_retry.json"),
    ]
    return subprocess.call(command, cwd=BACKEND)


if __name__ == "__main__":
    raise SystemExit(main())
