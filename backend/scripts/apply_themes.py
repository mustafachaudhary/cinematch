"""Backfill Letterboxd themes and TMDB-keyword fallbacks into the catalog.

Themes are keyed by ``tmdb_id`` through the resolved Letterboxd slug, never by
title alone. Existing Letterboxd theme labels get weight 1.0; TMDB keywords
are a lower-weight fallback (0.6). The canonical id is a slugified label so
the same theme text collapses across films.

Usage:
    cd backend && ../.venv/bin/python scripts/apply_themes.py \
        --db /tmp/opencode/.../copy.db --allow-live
    ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
DEFAULT_THEMES = BACKEND / "letterboxd_data" / "browser_scraped_themes.json"
DEFAULT_RESOLUTION = BACKEND / "letterboxd_data" / "seeds" / "resolution.db"

KEYWORD_STOPLIST = {
    "aftercreditsstinger",
    "duringcreditsstinger",
    "based on novel",
    "based on book",
    "based on play",
    "based on comic",
    "based on video game",
    "woman director",
    "short film",
    "behind the scenes",
}

TEXT_STOPWORDS = {
    "about", "after", "again", "against", "along", "also", "among", "another",
    "around", "because", "before", "being", "between", "both", "could", "does",
    "during", "each", "every", "film", "films", "first", "from", "have", "into",
    "itself", "just", "life", "like", "made", "make", "many", "more", "most",
    "movie", "much", "must", "never", "only", "other", "over", "own", "same",
    "short", "should", "some", "story", "such", "than", "that", "their", "them",
    "then", "there", "these", "they", "this", "those", "through", "under",
    "until", "very", "want", "well", "were", "what", "when", "where", "which",
    "while", "will", "with", "without", "would", "young",
}


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _theme_id(label: str) -> str:
    return slugify(label)


def canonical_themes(labels, *, source: str, weight: float, genres: set[str]):
    out = []
    seen = set()
    for label in labels or []:
        label = (label or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen or key in genres:
            continue
        seen.add(key)
        out.append(
            {
                "id": f"{'lb' if source == 'letterboxd' else 'kw'}:{_theme_id(label)}",
                "label": label,
                "weight": weight,
                "source": source,
            }
        )
    return out


def load_theme_jsonl(path: Path):
    """Load the portable ``{tmdb_id, themes:[{label,...}]}`` output."""
    by_tmdb = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        tmdb_id = row.get("tmdb_id")
        if not tmdb_id:
            continue
        labels = []
        for theme in row.get("themes") or []:
            label = theme.get("label") if isinstance(theme, dict) else str(theme)
            if label and label.strip():
                labels.append(label.strip())
        by_tmdb[int(tmdb_id)] = {
            "themes": labels,
            "genres": set(),
            "title": row.get("title"),
            "year": row.get("year"),
            "source": row.get("source"),
        }
    return by_tmdb


def load_letterboxd_themes(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_slug = {}
    by_title_year = {}
    for title, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        themes = payload.get("themes") or []
        if not themes:
            continue
        url = payload.get("url") or ""
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        slug = parts[1] if len(parts) >= 2 and parts[0] == "film" else None
        genres = {str(g).strip().casefold() for g in payload.get("genres") or []}
        item = {"themes": themes, "genres": genres, "title": title, "year": payload.get("year")}
        if slug:
            by_slug[slug] = item
        year = None
        try:
            year = int(payload.get("year"))
        except (TypeError, ValueError):
            pass
        by_title_year[(normalize_title(title), year)] = item
    return by_slug, by_title_year


def seed_slugs(resolution_path: Path) -> dict[int, str]:
    if not resolution_path.exists():
        return {}
    con = sqlite3.connect(f"file:{resolution_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            select tmdb_id, slug from resolution
            where status like 'resolved%' and tmdb_id is not null
            order by case status when 'resolved_catalog' then 0 else 1 end, updated_at
            """
        ).fetchall()
    finally:
        con.close()
    out = {}
    for tmdb_id, slug in rows:
        out.setdefault(int(tmdb_id), slug)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--themes", type=Path, default=DEFAULT_THEMES)
    parser.add_argument("--legacy-themes", type=Path, default=DEFAULT_THEMES)
    parser.add_argument("--resolution-db", type=Path, default=DEFAULT_RESOLUTION)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live:
        print("refusing live DB without --allow-live", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    by_tmdb = {}
    by_slug, by_title_year = {}, {}
    if args.themes.suffix == ".jsonl":
        by_tmdb = load_theme_jsonl(args.themes)
        if args.legacy_themes.exists():
            by_slug, by_title_year = load_letterboxd_themes(args.legacy_themes)
    else:
        by_slug, by_title_year = load_letterboxd_themes(args.themes)
    slugs = seed_slugs(args.resolution_db)
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("pragma busy_timeout=30000")
    con.execute("pragma journal_mode=wal")
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "select tmdb_id, title, release_year, genres, keywords, letterboxd_slug, overview from movie_cache"
    ).fetchall()
    seed_ids = {
        int(row[0])
        for row in con.execute("select distinct tmdb_id from logged_movies")
    }

    counts = Counter()
    seed_counts = Counter()
    updates = []
    slug_updates = []
    missing = []
    for row in rows:
        tmdb_id = int(row["tmdb_id"])
        slug = row["letterboxd_slug"] or slugs.get(tmdb_id)
        if slug and not row["letterboxd_slug"]:
            slug_updates.append((slug, tmdb_id))
        genres = {
            g.strip().casefold()
            for g in (row["genres"] or "").split(",")
            if g.strip()
        }
        item = by_tmdb.get(tmdb_id) if by_tmdb else None
        if not item and slug:
            item = by_slug.get(slug)
        if not item:
            item = by_title_year.get((normalize_title(row["title"]), row["release_year"]))
        source = None
        themes = []
        if item and item["themes"]:
            themes = canonical_themes(
                item["themes"], source="letterboxd", weight=1.0, genres=genres
            )
            source = "letterboxd"
        if not themes:
            try:
                keywords = json.loads(row["keywords"] or "[]")
            except ValueError:
                keywords = []
            keywords = [k for k in keywords if str(k).strip().casefold() not in KEYWORD_STOPLIST]
            themes = canonical_themes(
                keywords[:8], source="tmdb_keyword", weight=0.6, genres=genres
            )
            source = "tmdb_keyword" if themes else None
        if not themes:
            # Last resort: use the film's own genres as low-weight themes so
            # every film with metadata has at least one interpretable hook.
            genre_labels = [
                g.strip()
                for g in (row["genres"] or "").split(",")
                if g.strip()
            ]
            genre_labels = list(dict.fromkeys(genre_labels))[:4]
            if genre_labels:
                themes = [
                    {
                        "id": f"gf:{_theme_id(label)}",
                        "label": label,
                        "weight": 0.35,
                        "source": "genre_fallback",
                    }
                    for label in genre_labels
                ]
                source = "genre_fallback"
        if not themes:
            text = f"{row['title'] or ''} {row['overview'] or ''}".lower()
            tokens = [
                token
                for token in re.findall(r"[a-z][a-z'-]{4,}", text)
                if token not in TEXT_STOPWORDS
            ]
            labels = [
                label
                for label, _ in Counter(tokens).most_common(3)
            ] or ["Uncategorized"]
            themes = [
                {
                    "id": f"tf:{_theme_id(label)}",
                    "label": label.title(),
                    "weight": 0.25,
                    "source": "text_fallback",
                }
                for label in labels
            ]
            source = "text_fallback"

        if not themes:
            missing.append({"tmdb_id": tmdb_id, "title": row["title"], "year": row["release_year"]})
            counts["missing"] += 1
            if tmdb_id in seed_ids:
                seed_counts["missing"] += 1
            continue

        counts[source] += 1
        if tmdb_id in seed_ids:
            seed_counts[source] += 1
        updates.append(
            (
                json.dumps(themes, ensure_ascii=False),
                "themes-v1",
                tmdb_id,
            )
        )

    if not args.dry_run:
        if slug_updates:
            con.executemany(
                "update movie_cache set letterboxd_slug = ? where tmdb_id = ?",
                slug_updates,
            )
        if updates:
            con.executemany(
                """
                update movie_cache
                set themes = ?, text_version = ?
                where tmdb_id = ?
                """,
                updates,
            )
        con.commit()

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "applied": bool(not args.dry_run),
        "catalog": len(rows),
        "seed_films": len(seed_ids),
        "with_themes": counts["letterboxd"] + counts["tmdb_keyword"] + counts["genre_fallback"] + counts["text_fallback"],
        "letterboxd_themes": counts["letterboxd"],
        "tmdb_keyword_fallback": counts["tmdb_keyword"],
        "genre_fallback": counts["genre_fallback"],
        "text_fallback": counts["text_fallback"],
        "missing": counts["missing"],
        "seed_with_themes": seed_counts["letterboxd"] + seed_counts["tmdb_keyword"] + seed_counts["genre_fallback"] + seed_counts["text_fallback"],
        "seed_letterboxd_themes": seed_counts["letterboxd"],
        "seed_tmdb_keyword_fallback": seed_counts["tmdb_keyword"],
        "seed_genre_fallback": seed_counts["genre_fallback"],
        "seed_text_fallback": seed_counts["text_fallback"],
        "seed_missing": seed_counts["missing"],
        "coverage": round(
            (counts["letterboxd"] + counts["tmdb_keyword"] + counts["genre_fallback"] + counts["text_fallback"]) / len(rows), 4
        )
        if rows
        else 0,
        "seed_coverage": round(
            (seed_counts["letterboxd"] + seed_counts["tmdb_keyword"] + seed_counts["genre_fallback"] + seed_counts["text_fallback"]) / len(seed_ids), 4
        )
        if seed_ids
        else 0,
        "missing_examples": missing[:20],
    }
    print(json.dumps(report, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
