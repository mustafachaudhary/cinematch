import csv
from datetime import datetime
from pathlib import Path

from models.database import WatchedMovie


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
    value = (value or "").strip()
    if not value:
        return None
    try:
        rating = float(value)
    except ValueError:
        return None
    return rating if 0.5 <= rating <= 5.0 else None


def upsert_watched_row(db, row, rating=None):
    title = (row.get("Name") or "").strip()
    if not title:
        return False

    year = parse_year(row.get("Year"))
    watched = db.query(WatchedMovie).filter(
        WatchedMovie.title == title,
        WatchedMovie.release_year == year,
    ).first()

    if not watched:
        watched = WatchedMovie(
            title=title,
            release_year=year,
            letterboxd_uri=(row.get("Letterboxd URI") or "").strip() or None,
            watched_at=parse_date(row.get("Date")),
            rating=rating,
        )
        db.add(watched)
        return True

    if rating is not None:
        watched.rating = rating
    if not watched.letterboxd_uri:
        watched.letterboxd_uri = (row.get("Letterboxd URI") or "").strip() or None
    if not watched.watched_at:
        watched.watched_at = parse_date(row.get("Date"))
    return False


def sync_local_letterboxd_history(db, data_dir="letterboxd_data"):
    """Sync lightweight watched/rated history from exported Letterboxd CSVs."""
    base = Path(data_dir)
    if not base.exists():
        return {"watched_added": 0, "ratings_applied": 0}

    watched_added = 0
    watched_csv = base / "watched.csv"
    if watched_csv.exists():
        with watched_csv.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if upsert_watched_row(db, row):
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
                upsert_watched_row(db, row, rating=rating)
                ratings_applied += 1

    db.commit()
    return {"watched_added": watched_added, "ratings_applied": ratings_applied}
