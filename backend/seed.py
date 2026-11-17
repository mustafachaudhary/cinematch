#!/usr/bin/env python3
"""Seed the CineMatch catalog.

Pulls a broad, deduplicated pool of movies from TMDB (popular + top-rated +
per-genre discovery), caches them with metadata, and precomputes real
embeddings for every movie so search/recommendation are instant at runtime.

Usage:  python seed.py [--pages N]
"""

import argparse
import logging
import sys
import time

from models.database import MovieCache, SessionLocal, init_db
from services import tmdb
from services.embeddings import embed_movies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cinematch.seed")

# TMDB genre ids worth covering for a well-rounded catalog.
GENRE_IDS = [28, 12, 16, 35, 80, 99, 18, 10751, 14, 36, 27, 10402,
             9648, 10749, 878, 10770, 53, 10752, 37]


def upsert_movies(db, movie_data):
    added = 0
    existing_ids = {row[0] for row in db.query(MovieCache.tmdb_id).all()}
    for m in movie_data:
        if m["tmdb_id"] in existing_ids:
            continue
        db.add(
            MovieCache(
                tmdb_id=m["tmdb_id"],
                title=m["title"],
                overview=m.get("overview") or "",
                genres=",".join(m.get("genres") or []),
                poster_path=m.get("poster_path"),
                release_year=m.get("release_year"),
                popularity=m.get("popularity", 0.0),
                vote_average=m.get("vote_average", 0.0),
            )
        )
        existing_ids.add(m["tmdb_id"])
        added += 1
    db.commit()
    return added


def main(pages_per_source=3):
    init_db()
    if not tmdb.TMDB_API_KEY:
        log.error("TMDB_API_KEY missing — set it in backend/.env")
        sys.exit(1)

    db = SessionLocal()
    try:
        total_before = db.query(MovieCache).count()

        log.info("Fetching popular + top-rated movies…")
        pool = tmdb.fetch_popular_movies(pages=pages_per_source)
        time.sleep(0.3)
        pool += tmdb.fetch_top_rated_movies(pages=pages_per_source)
        time.sleep(0.3)

        log.info("Fetching per-genre discovery pages…")
        genre_map = tmdb.get_genre_map()
        for gid in GENRE_IDS:
            name = genre_map.get(gid, str(gid))
            try:
                pool += tmdb.fetch_genre_movies(gid, pages=2)
            except Exception as exc:
                log.warning("Genre %s failed: %s", name, exc)
            time.sleep(0.25)

        unique = {m["tmdb_id"]: m for m in pool}
        added = upsert_movies(db, list(unique.values()))
        total_after = db.query(MovieCache).count()

        log.info(
            "Catalog: %d → %d movies (%d new from %d fetched)",
            total_before, total_after, added, len(unique),
        )

        stats = embed_movies(db)
        log.info("Embeddings: %s", stats)

        missing_posters = db.query(MovieCache).filter(MovieCache.poster_path.is_(None)).count()
        log.info(
            "Done. %d movies ready (%d without posters).",
            total_after, missing_posters,
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the CineMatch catalog")
    parser.add_argument("--pages", type=int, default=3, help="pages per source list")
    args = parser.parse_args()
    main(args.pages)
