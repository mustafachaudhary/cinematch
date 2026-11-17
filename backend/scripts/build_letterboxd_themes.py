"""Join the public Letterboxd dataset themes to the local catalog.

The Kaggle ``gsimonx37/letterboxd`` snapshot (June 2024) contains ~940k
Letterboxd films and ~125k film/theme rows.  It covers most of the catalog
without a single page request.  Films released after the snapshot, or missing
from it, are written to a missing list for the incremental page scraper.

Output is a portable JSONL file keyed by TMDB id, so ``apply_themes.py`` can
apply it to either the live DB or a copy.

Usage:
    cd backend && ../.venv/bin/python scripts/build_letterboxd_themes.py \
        --movies-csv /tmp/opencode/letterboxd_dataset/movies.csv \
        --themes-csv /tmp/opencode/letterboxd_dataset/themes.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_TOP = BACKEND / "letterboxd_data" / "top_logged"


def normalize_title(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


def load_movies(path: Path):
    movies: dict[int, tuple[str, int | None]] = {}
    by_key: dict[tuple[str, int | None], list[int]] = defaultdict(list)
    by_title: dict[str, list[int]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                year = int((row.get("date") or "")[:4])
            except (TypeError, ValueError):
                year = None
            movie_id = int(row["id"])
            title = row.get("name") or ""
            movies[movie_id] = (title, year)
            key = normalize_title(title)
            by_key[(key, year)].append(movie_id)
            by_title[key].append(movie_id)
    return movies, by_key, by_title


def load_themes(path: Path):
    themes: dict[int, list[str]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            theme = (row.get("theme") or "").strip()
            if theme:
                themes[int(row["id"])].append(theme)
    return themes


def dataset_match(movies, by_key, by_title, themes, title, year):
    key = normalize_title(title)
    candidates = by_key.get((key, year)) or []
    if not candidates:
        candidates = by_title.get(key) or []
        if len(candidates) > 1 and year:
            near = [
                movie_id
                for movie_id in candidates
                if movies.get(movie_id, ("", None))[1] is not None
                and abs(movies[movie_id][1] - year) <= 1
            ]
            if len(near) == 1:
                candidates = near
    candidates = [movie_id for movie_id in candidates if themes.get(movie_id)]
    if not candidates:
        return None, None
    # Duplicate title/year entries are rare.  Prefer the one with the richest
    # theme list; the importer records this as a dataset choice.
    best = max(candidates, key=lambda movie_id: len(themes[movie_id]))
    return best, themes[best]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movies-csv", type=Path, required=True)
    parser.add_argument("--themes-csv", type=Path, required=True)
    parser.add_argument("--top-dir", type=Path, default=DEFAULT_TOP)
    parser.add_argument("--db", type=Path, default=BACKEND / "cinematch.db")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_TOP / "letterboxd_themes.jsonl"
    )
    parser.add_argument(
        "--missing-out", type=Path, default=DEFAULT_TOP / "themes_missing.jsonl"
    )
    args = parser.parse_args()

    movies, by_key, by_title = load_movies(args.movies_csv)
    dataset_themes = load_themes(args.themes_csv)
    print(
        json.dumps(
            {
                "dataset_movies": len(movies),
                "dataset_theme_films": len(dataset_themes),
                "dataset_theme_rows": sum(len(v) for v in dataset_themes.values()),
            },
            indent=2,
        )
    )

    resolution = sqlite3.connect(args.top_dir / "resolution.db")
    resolution.row_factory = sqlite3.Row
    top_rows = [
        dict(row)
        for row in resolution.execute(
            "select rank, slug, title, year, tmdb_id, status, payload from resolution "
            "where tmdb_id is not null order by rank"
        )
    ]
    resolution.close()

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    catalog_rows = [
        dict(row)
        for row in db.execute(
            "select tmdb_id, title, release_year, letterboxd_slug from movie_cache"
        )
    ]
    db.close()

    # Resolution rows carry the harvested title/year and the authoritative page
    # themes for fallback/repair rows.  The catalog adds films outside top 20k.
    targets: dict[int, dict] = {}
    for row in top_rows:
        targets[int(row["tmdb_id"])] = {
            "tmdb_id": int(row["tmdb_id"]),
            "slug": row.get("slug"),
            "title": row.get("title"),
            "year": row.get("year"),
            "payload": row.get("payload"),
            "rank": row.get("rank"),
        }
    for row in catalog_rows:
        tmdb_id = int(row["tmdb_id"])
        target = targets.setdefault(
            tmdb_id,
            {
                "tmdb_id": tmdb_id,
                "slug": row.get("letterboxd_slug"),
                "title": row.get("title"),
                "year": row.get("release_year"),
                "payload": None,
                "rank": None,
            },
        )
        if target.get("slug") is None:
            target["slug"] = row.get("letterboxd_slug")

    stats = Counter()
    out_rows = []
    missing = []
    for tmdb_id, target in targets.items():
        payload = target.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = None
        cached_themes = (payload or {}).get("themes") if isinstance(payload, dict) else None
        if cached_themes:
            themes = [
                {
                    "label": item.get("label"),
                    "kind": item.get("kind") or "theme",
                    "source": "letterboxd_page_cached",
                }
                for item in cached_themes
                if item.get("label")
            ]
            stats["cached_page"] += 1
            out_rows.append(
                {
                    "tmdb_id": tmdb_id,
                    "slug": target.get("slug"),
                    "title": target.get("title"),
                    "year": target.get("year"),
                    "themes": themes,
                    "source": "letterboxd_page_cached",
                }
            )
            continue

        dataset_id, labels = dataset_match(
            movies,
            by_key,
            by_title,
            dataset_themes,
            target.get("title"),
            target.get("year"),
        )
        if labels:
            stats["dataset"] += 1
            out_rows.append(
                {
                    "tmdb_id": tmdb_id,
                    "slug": target.get("slug"),
                    "title": target.get("title"),
                    "year": target.get("year"),
                    "themes": [
                        {"label": label, "kind": "theme", "source": "letterboxd_dataset"}
                        for label in labels
                    ],
                    "source": "letterboxd_dataset",
                    "dataset_id": dataset_id,
                }
            )
        else:
            stats["missing"] += 1
            missing.append(
                {
                    "tmdb_id": tmdb_id,
                    "slug": target.get("slug"),
                    "title": target.get("title"),
                    "year": target.get("year"),
                    "rank": target.get("rank"),
                }
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in out_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with args.missing_out.open("w", encoding="utf-8") as handle:
        for row in missing:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "catalog_targets": len(targets),
        "with_themes": len(out_rows),
        "dataset": stats["dataset"],
        "cached_page": stats["cached_page"],
        "missing": len(missing),
        "with_slug_missing": sum(1 for row in missing if row.get("slug")),
        "output": str(args.out),
        "missing_output": str(args.missing_out),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
