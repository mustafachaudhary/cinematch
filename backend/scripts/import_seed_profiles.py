"""Import the resolved Letterboxd seed cohort into an app DB.

The importer is deliberately conservative:

* it refuses to write to the live DB unless ``--allow-live`` is passed;
* it writes one transaction per seed user, so a failure cannot leave a
  half-imported profile;
* it snapshots mustafa's password/counts before and after and aborts if any
  invariant changed;
* it never imports a raw Letterboxd handle — only the pseudonym is stored.

Prerequisites: the target DB must already have the additive ``user_likes`` and
``seed_profile`` tables (run ``init_db`` against the copy first).

Usage:
    cd backend && DATABASE_URL=sqlite:////tmp/opencode/.../copy.db \
        ../.venv/bin/python -c 'from models.database import init_db; init_db()'
    ../.venv/bin/python scripts/import_seed_profiles.py --db /tmp/.../copy.db

The manifest is written to ``backend/reports/seed_import_<tag>.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from resolve_seed_ratings import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_SEEDS,
    film_key,
    load_harvest,
    load_mustafa_title_year,
    select_import_set,
)

LIVE_DB = (BACKEND / "cinematch.db").resolve()
REQUIRED_TABLES = {
    "users",
    "movie_cache",
    "logged_movies",
    "ratings",
    "films",
    "watched_movies",
    "user_profile",
    "user_likes",
    "seed_profile",
}


def table_names(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def mustafa_snapshot(con: sqlite3.Connection) -> dict:
    row = con.execute(
        "select user_id, password_hash from users where username = 'mustafa'"
    ).fetchone()
    if not row:
        raise RuntimeError("mustafa account is missing from the target DB")
    user_id, password_hash = row
    logged = con.execute(
        "select tmdb_id, rating from logged_movies where user_id = ? order by tmdb_id",
        (user_id,),
    ).fetchall()
    watched = con.execute(
        "select title, release_year from watched_movies where user_id = ? order by title, release_year",
        (user_id,),
    ).fetchall()
    encoded = json.dumps([[int(t), float(r)] for t, r in logged], separators=(",", ":"))
    return {
        "user_id": user_id,
        "password_hash_sha256": hashlib.sha256(
            (password_hash or "").encode("utf-8")
        ).hexdigest(),
        "logged": len(logged),
        "watched": len(watched),
        "logged_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def load_resolution(path: Path) -> dict[str, tuple[int, dict]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        resolved = {}
        for key, tmdb_id, payload in con.execute(
            """
            select film_key, tmdb_id, payload
            from resolution
            where status like 'resolved%' and tmdb_id is not null
            """
        ):
            if not payload:
                continue
            resolved[key] = (int(tmdb_id), json.loads(payload))
        return resolved
    finally:
        con.close()


def resolution_counts(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            status: count
            for status, count in con.execute(
                "select status, count(*) from resolution group by status"
            )
        }
    finally:
        con.close()


def upsert_catalog(con: sqlite3.Connection, payloads: dict[int, dict]) -> int:
    # The catalog is small (~2k rows before import), so loading every id is
    # cheaper and safer than a giant parameterized IN (...) clause.
    existing = {row[0] for row in con.execute("select tmdb_id from movie_cache")}
    inserted = 0
    for tmdb_id, movie in payloads.items():
        if tmdb_id in existing:
            continue
        con.execute(
            """
            insert into movie_cache
                (tmdb_id, title, overview, genres, poster_path, release_year,
                 popularity, vote_average, embedding, cached_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, null, current_timestamp)
            on conflict(tmdb_id) do nothing
            """,
            (
                tmdb_id,
                movie.get("title") or "",
                movie.get("overview") or "",
                ",".join(movie.get("genres") or []),
                movie.get("poster_path"),
                movie.get("release_year"),
                movie.get("popularity") or 0.0,
                movie.get("vote_average") or 0.0,
            ),
        )
        inserted += 1
    return inserted


def get_or_create_seed_user(con: sqlite3.Connection, pseudo: str) -> int:
    if not pseudo.startswith("lb_") or pseudo == "mustafa":
        raise RuntimeError(f"refusing non-pseudonymous seed username: {pseudo!r}")
    row = con.execute(
        "select user_id from users where username = ?", (pseudo,)
    ).fetchone()
    if row:
        user_id = int(row[0])
        con.execute("update users set is_seed = 1 where user_id = ?", (user_id,))
        return user_id
    cursor = con.execute(
        "insert into users (username, is_seed, created_at) values (?, 1, current_timestamp)",
        (pseudo,),
    )
    return int(cursor.lastrowid)


def clear_seed_rows(con: sqlite3.Connection, user_id: int) -> None:
    for table in (
        "logged_movies",
        "ratings",
        "watched_movies",
        "user_likes",
        "seed_profile",
    ):
        con.execute(f"delete from {table} where user_id = ?", (user_id,))


def build_profile_rows(profile: dict, resolved: dict[str, tuple[int, dict]]):
    """Return de-duplicated rows for one seed profile."""
    ratings: dict[int, dict] = {}
    watched_unrated: dict[tuple[str, int | None], dict] = {}
    liked: dict[int, dict] = {}
    unresolved_ratings = unresolved_likes = 0

    for row in profile["rows"]:
        key = film_key(row["slug"], row.get("year"))
        match = resolved.get(key)
        if not match:
            if row.get("rating") is not None:
                unresolved_ratings += 1
            continue
        tmdb_id, movie = match
        if row.get("rating") is not None:
            ratings[tmdb_id] = {"rating": float(row["rating"]), "movie": movie}
        else:
            resolved_year = movie.get("release_year")
            if resolved_year is None:
                resolved_year = row.get("year")
            title_year = (movie.get("title") or row.get("title"), resolved_year)
            watched_unrated[title_year] = {
                "tmdb_id": tmdb_id,
                "title": title_year[0],
                "year": title_year[1],
                "slug": row["slug"],
            }

    for row in profile["liked"]:
        key = film_key(row["slug"], row.get("year"))
        match = resolved.get(key)
        if not match:
            unresolved_likes += 1
            continue
        tmdb_id, movie = match
        liked[tmdb_id] = {"movie": movie}

    return ratings, watched_unrated, liked, unresolved_ratings, unresolved_likes


def import_profile(
    con: sqlite3.Connection,
    profile: dict,
    resolved: dict[str, tuple[int, dict]],
) -> dict:
    pseudo = profile["pseudonym"]
    state = profile["state"]
    flags = state.get("films", {}).get("flags") or []
    sampled = 1 if "sampled_cap" in flags else 0
    user_id = get_or_create_seed_user(con, pseudo)
    clear_seed_rows(con, user_id)

    ratings, watched_unrated, liked, unresolved_ratings, unresolved_likes = (
        build_profile_rows(profile, resolved)
    )

    con.execute("delete from user_profile where username = ?", (pseudo,))
    con.execute(
        """
        insert into user_profile (username, display_name, bio, created_at, updated_at)
        values (?, ?, '', current_timestamp, current_timestamp)
        """,
        (pseudo, f"Seed {pseudo[3:9]}"),
    )
    con.execute(
        """
        insert into seed_profile
            (user_id, cohort, handle_hash, sampled, n_items, imported_at)
        values (?, ?, ?, ?, ?, current_timestamp)
        """,
        (
            user_id,
            state.get("source") or "file",
            pseudo,
            sampled,
            len(profile["rows"]),
        ),
    )

    for tmdb_id, item in ratings.items():
        movie = item["movie"]
        con.execute(
            """
            insert into films (film_id, title, year)
            values (?, ?, ?)
            on conflict(film_id) do update set
                title = excluded.title,
                year = excluded.year
            """,
            (tmdb_id, movie.get("title") or "", movie.get("release_year")),
        )
        con.execute(
            """
            insert or replace into logged_movies
                (user_id, tmdb_id, rating, logged_at)
            values (?, ?, ?, current_timestamp)
            """,
            (user_id, tmdb_id, item["rating"]),
        )
        con.execute(
            """
            insert or replace into ratings (user_id, film_id, rating, rated_at)
            values (?, ?, ?, current_timestamp)
            """,
            (user_id, tmdb_id, item["rating"]),
        )

    for item in watched_unrated.values():
        con.execute(
            """
            insert or ignore into watched_movies
                (user_id, tmdb_id, title, release_year, rating,
                 letterboxd_uri, watched_at, imported_at)
            values (?, ?, ?, ?, null, ?, null, current_timestamp)
            """,
            (
                user_id,
                item["tmdb_id"],
                item["title"],
                item["year"],
                f"https://letterboxd.com/film/{item['slug']}/",
            ),
        )

    for tmdb_id in liked:
        con.execute(
            """
            insert or ignore into user_likes (user_id, tmdb_id, liked_at)
            values (?, ?, null)
            """,
            (user_id, tmdb_id),
        )

    imported_ratings = [item["rating"] for item in ratings.values()]
    return {
        "pseudonym": pseudo,
        "cohort": state.get("source") or "file",
        "sampled": sampled,
        "total_watched": len(profile["rows"]),
        "total_rated": sum(1 for r in profile["rows"] if r.get("rating") is not None),
        "total_liked": len(profile["liked"]),
        "imported_ratings": len(imported_ratings),
        "imported_likes": len(liked),
        "imported_watched_unrated": len(watched_unrated),
        "unresolved_ratings": unresolved_ratings,
        "unresolved_likes": unresolved_likes,
        "n_5.0": sum(1 for value in imported_ratings if value == 5.0),
        "low_tail": sum(1 for value in imported_ratings if value <= 2.5),
        "distinct_values": len(set(imported_ratings)),
    }


def validate_profile_counts(rows: list[dict]) -> None:
    problems = []
    for row in rows:
        if row["imported_ratings"] < 100:
            problems.append(f"{row['pseudonym']}: {row['imported_ratings']} ratings < 100")
        if row["distinct_values"] < 3:
            problems.append(f"{row['pseudonym']}: {row['distinct_values']} distinct values < 3")
        if row["imported_ratings"] >= 100 and row["n_5.0"] == 0:
            problems.append(f"{row['pseudonym']}: no 5.0 rating")
        if (
            row["total_rated"] >= 200
            and row["low_tail"] == 0
        ):
            problems.append(f"{row['pseudonym']}: no low tail (<=2.5)")
    if problems:
        raise RuntimeError("seed validation failed:\n  " + "\n  ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seeds-dir", type=Path, default=DEFAULT_SEEDS)
    parser.add_argument(
        "--resolution-db", type=Path, default=DEFAULT_SEEDS / "resolution.db"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--min-profiles", type=int, default=80)
    parser.add_argument("--min-ratings", type=int, default=30_000)
    parser.add_argument("--limit-profiles", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="permit writing to backend/cinematch.db (default: copy only)",
    )
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live:
        print(
            "refusing to write the live DB; import into a copy first, or pass --allow-live",
            file=sys.stderr,
        )
        return 2
    if not db_path.exists():
        print(f"target DB does not exist: {db_path}", file=sys.stderr)
        return 2
    if not args.resolution_db.exists():
        print(f"resolution DB does not exist: {args.resolution_db}", file=sys.stderr)
        return 2

    started = time.monotonic()
    films, likes, profiles = load_harvest(args.seeds_dir, 100)
    mustafa_keys = load_mustafa_title_year(db_path)
    selected = select_import_set(films, likes, mustafa_keys)
    resolved = load_resolution(args.resolution_db)
    if args.limit_profiles:
        profiles = profiles[: args.limit_profiles]

    if len(profiles) < args.min_profiles:
        print(
            f"only {len(profiles)} valid profiles; need at least {args.min_profiles}",
            file=sys.stderr,
        )
        return 2

    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    missing = REQUIRED_TABLES - table_names(con)
    if missing:
        print(
            f"target DB is missing tables: {sorted(missing)}. "
            "Run init_db against the copy first.",
            file=sys.stderr,
        )
        con.close()
        return 2

    before = mustafa_snapshot(con)
    catalog_payloads: dict[int, dict] = {}
    for key in selected:
        match = resolved.get(key)
        if match:
            tmdb_id, movie = match
            catalog_payloads[tmdb_id] = movie

    print(
        f"target={db_path} profiles={len(profiles)} selected_films={len(selected)} "
        f"resolved_films={len(resolved)} catalog_payloads={len(catalog_payloads)}"
    )

    # Preflight every profile before writing anything. This catches a
    # resolution shortfall or a malformed profile while the target DB is
    # still untouched.
    preflight = []
    for profile in profiles:
        ratings, watched, liked, ur, ul = build_profile_rows(profile, resolved)
        values = [item["rating"] for item in ratings.values()]
        preflight.append(
            {
                "pseudonym": profile["pseudonym"],
                "total_rated": sum(
                    1 for r in profile["rows"] if r.get("rating") is not None
                ),
                "imported_ratings": len(values),
                "imported_likes": len(liked),
                "imported_watched_unrated": len(watched),
                "unresolved_ratings": ur,
                "unresolved_likes": ul,
                "distinct_values": len(set(values)),
                "n_5.0": sum(1 for value in values if value == 5.0),
                "low_tail": sum(1 for value in values if value <= 2.5),
            }
        )
    validate_profile_counts(preflight)
    preflight_ratings = sum(row["imported_ratings"] for row in preflight)
    if preflight_ratings < args.min_ratings:
        print(
            f"preflight resolved only {preflight_ratings} ratings; "
            f"need at least {args.min_ratings}",
            file=sys.stderr,
        )
        con.close()
        return 2

    if args.dry_run:
        for row in preflight:
            print(
                f"  {row['pseudonym']}: ratings={row['imported_ratings']} "
                f"likes={row['imported_likes']} watched_unrated={row['imported_watched_unrated']} "
                f"unresolved_ratings={row['unresolved_ratings']} "
                f"unresolved_likes={row['unresolved_likes']}"
            )
        print(f"preflight total ratings={preflight_ratings}")
        con.close()
        return 0

    profile_rows: list[dict] = []
    try:
        if catalog_payloads:
            con.execute("BEGIN")
            inserted = upsert_catalog(con, catalog_payloads)
            con.commit()
            print(f"catalog inserted={inserted} existing={len(catalog_payloads) - inserted}")

        for index, profile in enumerate(profiles, 1):
            con.execute("BEGIN")
            try:
                row = import_profile(con, profile, resolved)
                con.commit()
            except Exception:
                con.rollback()
                raise
            profile_rows.append(row)
            if index % 10 == 0 or index == len(profiles):
                print(f"  imported {index}/{len(profiles)} profiles")
    except Exception:
        con.rollback()
        con.close()
        raise

    total_ratings = sum(row["imported_ratings"] for row in profile_rows)
    total_likes = sum(row["imported_likes"] for row in profile_rows)
    validate_profile_counts(profile_rows)
    if total_ratings < args.min_ratings:
        print(
            f"imported {total_ratings} ratings; need at least {args.min_ratings}",
            file=sys.stderr,
        )
        con.close()
        return 2

    after = mustafa_snapshot(con)
    if before != after:
        print(
            "MUSTAFA INVARIANT VIOLATION\n"
            f"before={json.dumps(before, sort_keys=True)}\n"
            f"after={json.dumps(after, sort_keys=True)}",
            file=sys.stderr,
        )
        con.close()
        return 3

    totals = {
        "profiles": len(profile_rows),
        "imported_ratings": total_ratings,
        "imported_likes": total_likes,
        "imported_watched_unrated": sum(
            row["imported_watched_unrated"] for row in profile_rows
        ),
        "sampled_profiles": sum(1 for row in profile_rows if row["sampled"]),
        "resolved_films": len(resolved),
        "selected_films": len(selected),
    }
    manifest = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "seeds_dir": str(args.seeds_dir),
        "resolution_db": str(args.resolution_db),
        "resolution_counts": resolution_counts(args.resolution_db),
        "totals": totals,
        "mustafa_before": before,
        "mustafa_after": after,
        "profiles": profile_rows,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    out_path = args.out or (BACKEND / "reports" / f"seed_import_{args.tag}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"totals": totals, "manifest": str(out_path)}, indent=2))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
