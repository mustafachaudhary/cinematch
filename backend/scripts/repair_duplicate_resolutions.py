"""Repair ambiguous top-list resolutions with the authoritative Letterboxd id.

TMDB title/year search is intentionally conservative, but a handful of
same-title/same-year films (``Strays`` / ``The Strays``, ``Beast`` 2022,
``Swan Song`` 2021, the two 1931 ``Dracula`` films, ...) collapse onto one
TMDB movie id.  Letterboxd's film page exposes the exact TMDB id, so for every
row in an ambiguous title/year or duplicate-id group we fetch that page and
replace the search guess.

This script only edits ``resolution.db``.  It does not touch the catalog or the
live database.  Re-run ``resolve_top_catalog.py`` afterwards to regenerate
``resolved.jsonl`` and the summary.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from resolve_top_catalog import fetch_letterboxd_film, normalize_title  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resolution-db",
        type=Path,
        default=BACKEND / "letterboxd_data" / "top_logged" / "resolution.db",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.resolution_db)
    con.row_factory = sqlite3.Row
    rows = [dict(row) for row in con.execute("SELECT * FROM resolution ORDER BY rank")]

    by_key: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
    by_tmdb: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_key[(normalize_title(row["title"]), row.get("year"))].append(row)
        if row.get("tmdb_id"):
            by_tmdb[int(row["tmdb_id"])].append(row)

    targets: dict[str, dict] = {}
    for group in by_key.values():
        if len(group) > 1:
            for row in group:
                targets[row["slug"]] = row
    for group in by_tmdb.values():
        if len(group) > 1:
            for row in group:
                targets[row["slug"]] = row

    print(
        json.dumps(
            {
                "rows": len(rows),
                "ambiguous_title_year_groups": sum(1 for g in by_key.values() if len(g) > 1),
                "duplicate_id_groups": sum(1 for g in by_tmdb.values() if len(g) > 1),
                "targets": len(targets),
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )

    changed = []
    failed = []
    now = datetime.now(timezone.utc).isoformat()
    for slug, row in sorted(targets.items(), key=lambda item: item[1]["rank"]):
        try:
            page = fetch_letterboxd_film(slug)
        except Exception as exc:  # noqa: BLE001
            failed.append({"slug": slug, "error": repr(exc)[:300]})
            continue
        if not page or not page.get("tmdb_id"):
            failed.append({"slug": slug, "error": "no_tmdb_id_on_page"})
            continue
        old_id = row.get("tmdb_id")
        new_id = int(page["tmdb_id"])
        payload = {
            "tmdb_id": new_id,
            "title": page.get("title") or row["title"],
            "release_year": page.get("year") or row.get("year"),
            "themes": page.get("themes") or [],
            "rating_count": page.get("rating_count"),
            "letterboxd_slug": slug,
            "source": "letterboxd_film_page_duplicate_repair",
            "previous_tmdb_id": old_id,
        }
        changed.append(
            {
                "rank": row["rank"],
                "slug": slug,
                "title": row["title"],
                "year": row.get("year"),
                "old_tmdb_id": old_id,
                "new_tmdb_id": new_id,
                "page_title": page.get("title"),
                "page_year": page.get("year"),
            }
        )
        if args.dry_run:
            continue
        con.execute(
            """
            UPDATE resolution
               SET tmdb_id = ?, status = 'resolved_letterboxd_repair',
                   method = 'letterboxd_repair', reason = NULL,
                   payload = ?, updated_at = ?
             WHERE film_key = ?
            """,
            (new_id, json.dumps(payload, ensure_ascii=False), now, row["film_key"]),
        )
        con.commit()

    print(
        json.dumps(
            {
                "changed": changed,
                "failed": failed,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    con.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
