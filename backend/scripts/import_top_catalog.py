"""Import the resolved Letterboxd top-film list into the local catalog.

The popularity harvest and the TMDB resolution pass are separate, resumable
steps (``scrape_letterboxd_popular.py`` and ``resolve_top_catalog.py``).  This
script performs the one-time catalog write:

* makes a consistent SQLite backup before touching the live DB;
* applies the additive schema migrations (``movie_cache.assets`` and the
  popularity/neighborhood/subniche tables);
* imports only rows with a valid TMDB movie id;
* enriches new films with one full TMDB request (metadata, credits, keywords,
  related films, images, videos), and stores those relationships locally;
* retains every harvested row in ``letterboxd_popularity``, including skipped
  and unresolved entries, with a reason for auditability.

It is restart-safe: films already present in ``movie_cache`` are not fetched
again, and popularity rows are idempotently upserted.

Usage:
    cd backend && ../.venv/bin/python scripts/import_top_catalog.py \
        --db cinematch.db --allow-live
    ... --limit 25 --dry-run       # inspect validation without writing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
DEFAULT_TOP = BACKEND / "letterboxd_data" / "top_logged"
sys.path.insert(0, str(BACKEND))


def normalize_title(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


def load_resolution(path: Path) -> list[dict]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT rank, slug, title, year, tmdb_id,
                       status, method, reason, payload, candidates
                FROM resolution
                ORDER BY rank
                """
            )
        ]
    finally:
        con.close()
    for row in rows:
        for key in ("payload", "candidates"):
            raw = row.get(key)
            if raw:
                try:
                    row[key] = json.loads(raw)
                except (TypeError, ValueError):
                    row[key] = None
    return rows


def load_popular_metadata(top_dir: Path) -> dict[str, dict]:
    path = top_dir / "top_20000.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("slug"):
            out[row["slug"]] = row
    return out


def validate_payload(payload: dict, row: dict, max_year_delta: int) -> tuple[bool, str | None]:
    target = normalize_title(row.get("title"))
    candidates = {
        normalize_title(payload.get("title")),
        normalize_title(payload.get("original_title")),
    }
    if target not in candidates:
        return False, "title_mismatch"
    if row.get("year") and payload.get("release_year"):
        delta = abs(int(row["year"]) - int(payload["release_year"]))
        if delta > max_year_delta:
            return False, f"year_delta_{delta}"
    return True, None


def people_rows(payload: dict) -> list[dict]:
    tmdb_id = int(payload["tmdb_id"])
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()

    def add(person: dict, role: str, ordering: int | None = None) -> None:
        person_id = person.get("id")
        name = (person.get("name") or "").strip()
        if not person_id or not name:
            return
        key = (int(person_id), role)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "tmdb_id": tmdb_id,
                "person_id": int(person_id),
                "name": name,
                "role": role,
                "ordering": ordering,
                "source": "tmdb",
            }
        )

    for person in payload.get("crew") or []:
        job = person.get("job")
        if job == "Director":
            add(person, "director")
        elif job in {"Writer", "Screenplay", "Story"}:
            add(person, "writer")
    for person in payload.get("cast") or []:
        add(person, "cast", person.get("order"))
    return out


def related_rows(payload: dict) -> list[dict]:
    tmdb_id = int(payload["tmdb_id"])
    out = []
    seen: set[tuple[int, str]] = set()
    for item in payload.get("related") or []:
        neighbor = item.get("related_tmdb_id") or item.get("tmdb_id")
        source = item.get("source") or "similar"
        if not neighbor or int(neighbor) == tmdb_id:
            continue
        key = (int(neighbor), source)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "tmdb_id": tmdb_id,
                "related_tmdb_id": int(neighbor),
                "source": source,
                "rank": item.get("rank"),
            }
        )
    return out


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


def movie_values(payload: dict, row: dict) -> dict:
    return {
        "tmdb_id": int(payload["tmdb_id"]),
        "title": payload.get("title") or row["title"],
        "overview": payload.get("overview") or "",
        "genres": ", ".join(payload.get("genres") or []),
        "poster_path": payload.get("poster_path"),
        "release_year": payload.get("release_year"),
        "popularity": payload.get("popularity") or 0.0,
        "vote_average": payload.get("vote_average") or 0.0,
        "runtime": payload.get("runtime"),
        "original_language": payload.get("original_language"),
        "vote_count": payload.get("vote_count"),
        "collection_id": payload.get("collection_id"),
        "imdb_id": payload.get("imdb_id"),
        "letterboxd_slug": row.get("slug"),
        "keywords": json.dumps(payload.get("keywords") or [], ensure_ascii=False),
        "assets": assets_json(payload),
        "cached_at": datetime.utcnow(),
    }


def backup_db(db_path: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--top-dir", type=Path, default=DEFAULT_TOP)
    parser.add_argument("--resolution-db", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--rps", type=float, default=18.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--trust-resolution",
        action="store_true",
        help="skip title/year validation for rows whose TMDB id was read from Letterboxd",
    )
    parser.add_argument("--max-year-delta", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live and not args.dry_run:
        print("refusing live DB without --allow-live", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    resolution_db = args.resolution_db or (args.top_dir / "resolution.db")
    rows = load_resolution(resolution_db)
    popular_metadata = load_popular_metadata(args.top_dir)
    for row in rows:
        popular = popular_metadata.get(row["slug"]) or {}
        row["letterboxd_uid"] = popular.get("letterboxd_uid")
        row["average_rating"] = popular.get("average_rating")
    if args.limit:
        rows = rows[: args.limit]

    started = time.monotonic()
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = args.backup or (
        BACKEND / "reports" / f"cinematch_pre_top20k_{tag}.db"
    )

    if args.dry_run:
        # Validation and duplicate audit without importing or fetching.
        by_id: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            if row.get("tmdb_id"):
                by_id[int(row["tmdb_id"])].append(row)
        print(
            json.dumps(
                {
                    "db": str(db_path),
                    "resolution_db": str(resolution_db),
                    "rows": len(rows),
                    "resolved_ids": len(by_id),
                    "duplicate_id_groups": sum(1 for group in by_id.values() if len(group) > 1),
                    "unresolved": sum(1 for row in rows if not row.get("tmdb_id")),
                    "statuses": dict(Counter(row["status"] for row in rows)),
                },
                indent=2,
            )
        )
        return 0

    if db_path == LIVE_DB and not backup_path.exists():
        print(f"backing up live DB to {backup_path}")
        backup_db(db_path, backup_path)
        print(f"backup complete ({backup_path.stat().st_size / 1e6:.1f} MB)")

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    from models.database import (  # noqa: E402
        FilmPerson,
        FilmRelated,
        LetterboxdPopularity,
        MovieCache,
        SessionLocal,
        init_db,
    )
    from services import tmdb

    init_db()
    db = SessionLocal()
    existing_ids = {int(value) for (value,) in db.query(MovieCache.tmdb_id).all()}
    existing_rows = {
        int(movie.tmdb_id): movie
        for movie in db.query(MovieCache).filter(MovieCache.tmdb_id.in_(existing_ids)).all()
    } if existing_ids else {}
    existing_popularity = {
        row.rank: row for row in db.query(LetterboxdPopularity).all()
    }

    by_id: dict[int, list[dict]] = defaultdict(list)
    unresolved: list[dict] = []
    for row in rows:
        if row.get("tmdb_id"):
            by_id[int(row["tmdb_id"])].append(row)
        else:
            unresolved.append(row)

    new_ids = [tmdb_id for tmdb_id in by_id if tmdb_id not in existing_ids]
    print(
        json.dumps(
            {
                "rows": len(rows),
                "resolved_ids": len(by_id),
                "existing_ids": sum(1 for tmdb_id in by_id if tmdb_id in existing_ids),
                "new_ids": len(new_ids),
                "unresolved_rows": len(unresolved),
                "duplicate_id_groups": sum(1 for group in by_id.values() if len(group) > 1),
                "workers": args.workers,
            },
            indent=2,
        )
    )

    stats = {
        "rows": len(rows),
        "resolved_rows": len(rows) - len(unresolved),
        "unresolved_rows": len(unresolved),
        "new_ids": len(new_ids),
        "imported_new": 0,
        "imported_existing": 0,
        "skipped": 0,
        "title_mismatch": 0,
        "year_mismatch": 0,
        "invalid_tmdb": 0,
        "duplicate_rows": 0,
        "people_rows": 0,
        "related_rows": 0,
        "popularity_rows": 0,
        "errors": [],
    }

    def upsert_popularity(row: dict, status: str, note: str | None = None,
                          tmdb_id: int | None = None) -> None:
        key = int(row["rank"])
        pop = existing_popularity.get(key)
        values = {
            "slug": row["slug"],
            "title": row["title"],
            "year": row.get("year"),
            "tmdb_id": tmdb_id if tmdb_id is not None else row.get("tmdb_id"),
            "letterboxd_uid": row.get("letterboxd_uid"),
            "average_rating": row.get("average_rating"),
            "status": status,
            "source": "letterboxd_popular_csi_hide_tv",
            "notes": note,
            "imported_at": datetime.utcnow(),
        }
        if pop is None:
            pop = LetterboxdPopularity(**values)
            db.add(pop)
            existing_popularity[key] = pop
        else:
            for name, value in values.items():
                setattr(pop, name, value)
        stats["popularity_rows"] += 1

    def import_film(tmdb_id: int, payload: dict, group: list[dict]) -> None:
        valid_rows = []
        if args.trust_resolution:
            valid_rows = list(group)
        else:
            for row in group:
                ok, reason = validate_payload(payload, row, args.max_year_delta)
                if ok:
                    valid_rows.append(row)
                else:
                    stats["skipped"] += 1
                    if reason == "title_mismatch":
                        stats["title_mismatch"] += 1
                    elif reason and reason.startswith("year_delta"):
                        stats["year_mismatch"] += 1
                    upsert_popularity(row, "skipped", f"tmdb_{reason}")
        if not valid_rows:
            return

        primary = valid_rows[0]
        values = movie_values(payload, primary)
        movie = db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).one_or_none()
        if movie is None:
            movie = MovieCache(**values)
            db.add(movie)
            db.flush()
            stats["imported_new"] += 1
        # Existing films are never overwritten: their themes/embeddings are
        # already curated and a popularity refresh must not regress them.

        db.execute(FilmPerson.__table__.delete().where(FilmPerson.tmdb_id == tmdb_id))
        people = people_rows(payload)
        if people:
            db.execute(FilmPerson.__table__.insert(), people)
            stats["people_rows"] += len(people)

        db.execute(
            FilmRelated.__table__.delete().where(
                FilmRelated.tmdb_id == tmdb_id,
                FilmRelated.source.in_(("similar", "recommendations")),
            )
        )
        related = related_rows(payload)
        if related:
            db.execute(FilmRelated.__table__.insert(), related)
            stats["related_rows"] += len(related)

        for row in valid_rows:
            note = None
            if len(valid_rows) > 1:
                note = "multiple Letterboxd rows share this TMDB id"
                stats["duplicate_rows"] += 1
            upsert_popularity(row, "imported", note, tmdb_id=tmdb_id)

    # Existing catalog rows need no network call.  They still get a popularity
    # row and (only when empty) a Letterboxd slug.
    for tmdb_id, group in by_id.items():
        if tmdb_id not in existing_ids:
            continue
        movie = existing_rows.get(tmdb_id)
        for row in group:
            if movie is not None and not movie.letterboxd_slug:
                movie.letterboxd_slug = row["slug"]
            upsert_popularity(row, "imported_existing", "already in movie_cache", tmdb_id=tmdb_id)
        stats["imported_existing"] += 1

    for row in unresolved:
        upsert_popularity(row, "unresolved", row.get("reason") or "no_tmdb_match")

    db.commit()

    tmdb.configure_rate_limit(args.rps)
    pending = [tmdb_id for tmdb_id in new_ids]
    if pending:
        fetched = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(tmdb.get_movie_full, tmdb_id): tmdb_id
                for tmdb_id in pending
            }
            for future in as_completed(futures):
                tmdb_id = futures[future]
                try:
                    payload = future.result()
                except Exception as exc:  # noqa: BLE001
                    stats["errors"].append({"tmdb_id": tmdb_id, "error": repr(exc)[:300]})
                    payload = None
                fetched += 1
                group = by_id[tmdb_id]
                if not payload:
                    stats["invalid_tmdb"] += 1
                    for row in group:
                        upsert_popularity(row, "skipped", "tmdb_movie_not_found", tmdb_id=tmdb_id)
                else:
                    import_film(tmdb_id, payload, group)
                if fetched % 100 == 0 or fetched == len(pending):
                    db.commit()
                    elapsed = max(0.001, time.monotonic() - started)
                    print(
                        f"  fetched {fetched}/{len(pending)} "
                        f"new={stats['imported_new']} skipped={stats['skipped']} "
                        f"invalid={stats['invalid_tmdb']} rate={fetched / elapsed:.1f}/s"
                    )
        db.commit()

    # Ensure unresolved rows are persisted even when there were no network
    # candidates (e.g. a resolution DB with only unresolved entries).
    db.commit()

    stats["catalog_after"] = db.query(MovieCache).count()
    stats["letterboxd_popularity_rows"] = db.query(LetterboxdPopularity).count()
    stats["person_rows_total"] = db.query(FilmPerson).count()
    stats["related_rows_total"] = db.query(FilmRelated).count()
    stats["elapsed_seconds"] = round(time.monotonic() - started, 1)

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "backup": str(backup_path),
        "resolution_db": str(resolution_db),
        "stats": stats,
    }
    out_path = args.report or (BACKEND / "reports" / f"top_catalog_import_{tag}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    print(f"report: {out_path}")
    db.close()
    return 0 if not stats["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
