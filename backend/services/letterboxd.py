import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from models.database import WatchedMovie


def _norm_title(value):
    """Case/punctuation/whitespace-insensitive title key for catalog matching."""
    cleaned = "".join(
        ch if (ch.isalnum() or ch.isspace()) else " " for ch in (value or "").lower()
    )
    return " ".join(cleaned.split())


def parse_year(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def parse_rating(value):
    """Letterboxd ratings are 0.5–5 stars (0 = unrated)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        rating = float(value)
    except ValueError:
        return None
    if rating == 0:
        return None
    return rating if 0.5 <= rating <= 5.0 else None


def upsert_watched_row(db, row, user_id: int, rating=None, tmdb_id=None):
    title = (row.get("Name") or "").strip()
    if not title:
        return False

    year = parse_year(row.get("Year"))
    watched = (
        db.query(WatchedMovie)
        .filter(
            WatchedMovie.user_id == user_id,
            WatchedMovie.title == title,
            WatchedMovie.release_year == year,
        )
        .first()
    )

    if not watched:
        watched = WatchedMovie(
            user_id=user_id,
            tmdb_id=tmdb_id,
            title=title,
            release_year=year,
            letterboxd_uri=(row.get("Letterboxd URI") or "").strip() or None,
            watched_at=parse_date(row.get("Date")),
            rating=rating,
        )
        db.add(watched)
        return True

    if tmdb_id is not None and watched.tmdb_id is None:
        watched.tmdb_id = tmdb_id
    if rating is not None:
        watched.rating = rating
    if not watched.letterboxd_uri:
        watched.letterboxd_uri = (row.get("Letterboxd URI") or "").strip() or None
    if not watched.watched_at:
        watched.watched_at = parse_date(row.get("Date"))
    return False


def backfill_watched_tmdb_ids(db, user_id: int | None = None) -> int:
    """Resolve missing ``watched_movies.tmdb_id`` values against the catalog.

    Title matching is normalized (case/punctuation-insensitive) and
    conservative: years must agree within one when both sides have one, and
    ambiguous matches (same title, several candidates, no decisive year) are
    skipped rather than guessed. No TMDB calls — only films already in the
    catalog can be resolved. Returns the number of rows filled.
    """
    from models.database import MovieCache

    query = db.query(WatchedMovie).filter(WatchedMovie.tmdb_id.is_(None))
    if user_id is not None:
        query = query.filter(WatchedMovie.user_id == user_id)
    missing = query.all()
    if not missing:
        return 0

    catalog = defaultdict(list)
    for tmdb_id, title, year in db.query(
        MovieCache.tmdb_id, MovieCache.title, MovieCache.release_year
    ).all():
        catalog[_norm_title(title)].append((tmdb_id, year))

    resolved = 0
    for watched in missing:
        candidates = catalog.get(_norm_title(watched.title)) or []
        if watched.release_year is not None:
            candidates = [
                c for c in candidates
                if c[1] is None or abs(c[1] - watched.release_year) <= 1
            ]
        if not candidates:
            continue
        if len(candidates) > 1:
            exact = [c for c in candidates if c[1] == watched.release_year]
            if len(exact) != 1:
                continue  # ambiguous → leave unmatched
            candidates = exact
        watched.tmdb_id = candidates[0][0]
        resolved += 1

    if resolved:
        db.commit()
    return resolved


def sync_local_letterboxd_history(db, user_id: int, data_dir="backend/letterboxd_data"):
    """Import exported Letterboxd CSVs (watched.csv / ratings.csv) if present."""
    base = Path(data_dir)
    if not base.exists():
        base = Path("letterboxd_data")
    if not base.exists():
        return {"watched_added": 0, "ratings_applied": 0}

    watched_added = 0
    watched_csv = base / "watched.csv"
    if watched_csv.exists():
        with watched_csv.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if upsert_watched_row(db, row, user_id):
                    watched_added += 1
        db.commit()

    ratings_applied = 0
    ratings_csv = base / "ratings.csv"
    if ratings_csv.exists():
        with ratings_csv.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rating = parse_rating(row.get("Rating"))
                if rating is None:
                    continue
                upsert_watched_row(db, row, user_id, rating=rating)
                ratings_applied += 1

    db.commit()
    return {"watched_added": watched_added, "ratings_applied": ratings_applied}
