"""Flag resolved films whose title only matched after dropping an article.

The title+year resolver intentionally ignores a leading "The"/"A"/"An" for
same-year matches, but article stripping plus a ±1 release-year tolerance can
conflate unrelated films (e.g. Letterboxd's "ATM" 2017 and TMDB's
"The ATM" 2016). This audit turns those matches back into ``unresolved`` so the
IMDb fallback can resolve them exactly.

Usage:
    cd backend && ../.venv/bin/python scripts/repair_resolution.py
    ... --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from resolve_seed_ratings import exact_title  # noqa: E402

DEFAULT_DB = BACKEND / "letterboxd_data" / "seeds" / "resolution.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolution-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.resolution_db, timeout=30)
    con.execute("pragma busy_timeout=30000")
    rows = con.execute(
        """
        select film_key, slug, year, title, status, method, tmdb_id, payload
        from resolution
        where status like 'resolved%' and payload is not null
        order by title
        """
    ).fetchall()

    suspicious = []
    for film_key, slug, year, title, status, method, tmdb_id, payload in rows:
        movie = json.loads(payload)
        resolved_title = movie.get("title") or ""
        resolved_year = movie.get("release_year")
        if exact_title(title) == exact_title(resolved_title):
            continue
        year_delta = (
            abs(int(year) - int(resolved_year))
            if year is not None and resolved_year is not None
            else None
        )
        # Same-year article variants (e.g. "School of Rock" vs
        # "The School of Rock") are intentional. Off-by-one article variants
        # and yearless article matches are where the false positives live.
        if method in {"title_year_pm1", "title_year_pm1_margin", "title_only"} or (
            year_delta is not None and year_delta > 0
        ):
            suspicious.append(
                {
                    "film_key": film_key,
                    "slug": slug,
                    "year": year,
                    "title": title,
                    "method": method,
                    "tmdb_id": tmdb_id,
                    "resolved_title": resolved_title,
                    "resolved_year": resolved_year,
                }
            )

    print(
        json.dumps(
            {
                "resolution_db": str(args.resolution_db),
                "resolved_rows": len(rows),
                "suspicious": len(suspicious),
                "examples": suspicious[:12],
                "applied": bool(args.apply),
            },
            indent=2,
        )
    )
    if args.apply and suspicious:
        now = datetime.now(timezone.utc).isoformat()
        for row in suspicious:
            con.execute(
                """
                update resolution
                set status = 'unresolved',
                    method = null,
                    tmdb_id = null,
                    reason = 'article_year_mismatch',
                    payload = null,
                    updated_at = ?
                where film_key = ?
                """,
                (now, row["film_key"]),
            )
        con.commit()
        print(f"marked {len(suspicious)} rows unresolved")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
