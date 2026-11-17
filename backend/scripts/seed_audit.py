"""Read-only audit of seed / rating data quality for CineMatch.

Usage:
    cd backend && ../.venv/bin/python scripts/seed_audit.py \
        --out reports/seed_audit_<tag>.json

Reports (never mutates):
  * user / rating counts split into real users and Letterboxd seeds
  * per-seed rating histograms and corruption markers (legacy rated-(\\d) bug
    turns every 5.0 into 0.5; a seed with ratings but no 5.0 is suspicious)
  * catalog coverage (films with >=1/2/5 ratings, zero-rated films)
  * overlap between mustafa and the seed cohort
  * duplicate catalog titles (ambiguous remakes)
  * theme JSON coverage
  * watched-vs-rated gaps for real users
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

STARS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]


def _one(con: sqlite3.Connection, sql: str, params=()):
    row = con.execute(sql, params).fetchone()
    return row[0] if row else None


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"pragma table_info({table})")}


def audit(db_path: Path, themes_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    user_cols = _table_columns(con, "users")
    has_is_seed = "is_seed" in user_cols
    tables = {
        row[0]
        for row in con.execute("select name from sqlite_master where type = 'table'")
    }
    has_likes = "user_likes" in tables
    has_seed_profile = "seed_profile" in tables

    users = [dict(r) for r in con.execute(
        f"""
        select u.user_id, u.username, {"u.is_seed" if has_is_seed else "0 as is_seed"}
        from users u order by u.username
        """
    )]
    seed_users = [u for u in users if _is_seed_row(u, has_is_seed)]

    ratings = [dict(r) for r in con.execute(
        """
        select r.user_id, u.username, r.film_id, r.rating
        from ratings r join users u on u.user_id = r.user_id
        """
    )]
    ratings_by_user: dict[int, list[float]] = {}
    for row in ratings:
        ratings_by_user.setdefault(row["user_id"], []).append(float(row["rating"]))

    seed_ids = {u["user_id"] for u in seed_users}
    seed_ratings = [r for r in ratings if r["user_id"] in seed_ids]
    real_ratings = [r for r in ratings if r["user_id"] not in seed_ids]

    likes_by_user: dict[int, int] = {}
    liked_films = 0
    if has_likes:
        likes_by_user = {
            int(uid): int(count)
            for uid, count in con.execute(
                "select user_id, count(*) from user_likes group by user_id"
            )
        }
        liked_films = _one(con, "select count(distinct tmdb_id) from user_likes") or 0

    per_seed = []
    for user in seed_users:
        values = ratings_by_user.get(user["user_id"], [])
        hist = Counter(values)
        n = len(values)
        n5 = hist.get(5.0, 0)
        per_seed.append({
            "username": user["username"],
            "ratings": n,
            "likes": likes_by_user.get(user["user_id"], 0),
            "distinct_values": len(hist),
            "mean": round(sum(values) / n, 3) if n else None,
            "low_tail_pct": round(100 * sum(1 for v in values if v <= 2.5) / n, 1) if n else None,
            "n_0.5": hist.get(0.5, 0),
            "n_5.0": n5,
            "suspected_corrupt": n >= 50 and n5 == 0,
            "histogram": {str(k): hist.get(k, 0) for k in STARS},
        })
    per_seed.sort(key=lambda x: x["username"])

    rated_ids = {int(r["film_id"]) for r in ratings}
    cache_ids = {int(r[0]) for r in con.execute("select tmdb_id from movie_cache")}
    coverage = {
        "catalog_films": len(cache_ids),
        "rated_by_anyone": len(rated_ids & cache_ids),
        "gte_1": len(rated_ids & cache_ids),
        "gte_2": sum(
            1 for fid, count in Counter(int(r["film_id"]) for r in ratings).items()
            if count >= 2 and fid in cache_ids
        ),
        "gte_5": sum(
            1 for fid, count in Counter(int(r["film_id"]) for r in ratings).items()
            if count >= 5 and fid in cache_ids
        ),
        "zero_rated": len(cache_ids - rated_ids),
    }

    mustafa_id = _one(con, "select user_id from users where username = 'mustafa'")
    mustafa_rated = {int(r["film_id"]) for r in ratings if r["user_id"] == mustafa_id}
    seed_rated = {int(r["film_id"]) for r in seed_ratings}
    overlap = {
        "mustafa_ratings": len(mustafa_rated),
        "overlap_with_seeds": len(mustafa_rated & seed_rated),
        "pct_of_mustafa": round(100 * len(mustafa_rated & seed_rated) / len(mustafa_rated), 1)
        if mustafa_rated else 0,
    }

    dup_rows = con.execute(
        """
        select title, count(*) as n, group_concat(release_year, ',') as years
        from movie_cache group by lower(title) having n > 1
        order by n desc, title limit 25
        """
    ).fetchall()
    duplicates = [dict(r) for r in dup_rows]

    theme_db = {}
    movie_cols = _table_columns(con, "movie_cache")
    if "themes" in movie_cols:
        seed_film_ids = {
            int(r[0])
            for r in con.execute("select distinct tmdb_id from logged_movies")
        }
        catalog_ids = set(cache_ids)
        source_counts = Counter()
        themed_ids = set()
        for tmdb_id, raw in con.execute("select tmdb_id, themes from movie_cache"):
            try:
                parsed = json.loads(raw or "[]")
            except ValueError:
                parsed = []
            if not parsed:
                continue
            themed_ids.add(int(tmdb_id))
            for item in parsed:
                source = item.get("source") if isinstance(item, dict) else None
                source_counts[source or "unknown"] += 1
        theme_db = {
            "available": True,
            "catalog_films": len(catalog_ids),
            "catalog_with_themes": len(themed_ids & catalog_ids),
            "catalog_coverage": round(len(themed_ids & catalog_ids) / len(catalog_ids), 4)
            if catalog_ids
            else 0,
            "seed_films": len(seed_film_ids),
            "seed_with_themes": len(themed_ids & seed_film_ids),
            "seed_coverage": round(len(themed_ids & seed_film_ids) / len(seed_film_ids), 4)
            if seed_film_ids
            else 0,
            "sources": dict(source_counts),
        }

    themes = {}
    if themes_path.exists():
        raw = json.loads(themes_path.read_text())
        themed = 0
        title_matches = 0
        ambiguous = []
        cache_titles = Counter(
            r[0].strip().lower() for r in con.execute("select title from movie_cache")
        )
        for title, payload in raw.items():
            if isinstance(payload, dict) and payload.get("themes"):
                themed += 1
            key = str(title).strip().lower()
            if cache_titles.get(key, 0) >= 1:
                title_matches += 1
            if cache_titles.get(key, 0) > 1:
                ambiguous.append(title)
        themes = {
            "entries": len(raw),
            "with_themes": themed,
            "catalog_title_matches": title_matches,
            "ambiguous_titles": ambiguous[:15],
            "ambiguous_count": len(ambiguous),
        }

    watched = {}
    for user in users:
        if _is_seed_row(user, has_is_seed):
            continue
        rows = con.execute(
            "select tmdb_id, title, release_year from watched_movies where user_id = ?",
            (user["user_id"],),
        ).fetchall()
        with_tmdb = [r for r in rows if r["tmdb_id"]]
        own_rated = {
            int(r["film_id"]) for r in ratings if r["user_id"] == user["user_id"]
        }
        unrated = [r for r in with_tmdb if int(r["tmdb_id"]) not in own_rated]
        watched[user["username"]] = {
            "watched": len(rows),
            "with_tmdb_id": len(with_tmdb),
            "watched_unrated": len(unrated),
            "no_tmdb_id": len(rows) - len(with_tmdb),
        }

    invalid = [r for r in ratings if r["rating"] < 0.5 or r["rating"] > 5.0]
    orphan_users = [r for r in ratings if r["user_id"] not in {u["user_id"] for u in users}]
    orphan_films = [r for r in ratings if int(r["film_id"]) not in cache_ids]

    likes = {
        "total": sum(likes_by_user.values()),
        "liked_films": liked_films,
        "seeds_with_likes": sum(1 for s in per_seed if s["likes"] > 0),
        "seeds_zero_likes": [
            s["username"] for s in per_seed if s["likes"] == 0
        ],
    }
    seed_provenance = {"available": has_seed_profile}
    if has_seed_profile:
        seed_provenance.update(
            {
                "profiles": _one(con, "select count(*) from seed_profile") or 0,
                "sampled": _one(con, "select count(*) from seed_profile where sampled = 1") or 0,
            }
        )

    con.close()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "has_is_seed_column": has_is_seed,
        "totals": {
            "users": len(users),
            "real_users": len(users) - len(seed_users),
            "seed_users": len(seed_users),
            "ratings": len(ratings),
            "seed_ratings": len(seed_ratings),
            "real_ratings": len(real_ratings),
        },
        "per_seed": per_seed,
        "suspected_corrupt_seeds": [s["username"] for s in per_seed if s["suspected_corrupt"]],
        "coverage": coverage,
        "likes": likes,
        "seed_provenance": seed_provenance,
        "mustafa_overlap": overlap,
        "duplicates": duplicates,
        "themes": themes,
        "theme_coverage_db": theme_db,
        "watched": watched,
        "integrity": {
            "invalid_rating_values": len(invalid),
            "orphan_user_ratings": len(orphan_users),
            "orphan_film_ratings": len(orphan_films),
        },
    }


def _is_seed_row(user: dict, has_is_seed: bool) -> bool:
    if has_is_seed:
        return bool(user.get("is_seed"))
    return str(user.get("username", "")).startswith("lb_")


def print_summary(report: dict) -> None:
    t = report["totals"]
    c = report["coverage"]
    o = report["mustafa_overlap"]
    print(f"users {t['users']} (real {t['real_users']}, seeds {t['seed_users']})")
    print(f"ratings {t['ratings']} (seed {t['seed_ratings']}, real {t['real_ratings']})")
    print(
        f"coverage: {c['catalog_films']} catalog, {c['gte_2']} with >=2, "
        f"{c['gte_5']} with >=5, {c['zero_rated']} zero-rated"
    )
    print(
        f"mustafa: {o['mustafa_ratings']} ratings, {o['overlap_with_seeds']} overlap "
        f"with seeds ({o['pct_of_mustafa']}%)"
    )
    likes = report.get("likes") or {}
    if likes:
        print(
            f"likes: {likes['total']} rows, {likes['liked_films']} films, "
            f"{likes['seeds_with_likes']} seeds with likes, "
            f"{len(likes['seeds_zero_likes'])} zero-like seeds"
        )
    if report["suspected_corrupt_seeds"]:
        print(
            f"CORRUPT MARKERS: {len(report['suspected_corrupt_seeds'])} seeds have "
            f"ratings but no 5.0 -> {', '.join(report['suspected_corrupt_seeds'][:8])}"
        )
    else:
        print("CORRUPT MARKERS: none")
    themes = report["themes"]
    if themes:
        print(
            f"themes: {themes['entries']} entries, {themes['with_themes']} themed, "
            f"{themes['ambiguous_count']} ambiguous titles"
        )
    theme_db = report.get("theme_coverage_db") or {}
    if theme_db:
        print(
            f"theme coverage (DB): {theme_db['catalog_with_themes']}/"
            f"{theme_db['catalog_films']} catalog "
            f"({theme_db['catalog_coverage']:.1%}), "
            f"{theme_db['seed_with_themes']}/{theme_db['seed_films']} seed films "
            f"({theme_db['seed_coverage']:.1%}), sources="
            f"{json.dumps(theme_db['sources'], sort_keys=True)}"
        )
    print("watched:", json.dumps(report["watched"], sort_keys=True))
    print("integrity:", json.dumps(report["integrity"], sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "cinematch.db")
    parser.add_argument(
        "--themes",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "letterboxd_data" / "browser_scraped_themes.json",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--strict", action="store_true", help="exit 2 if corruption is detected")
    args = parser.parse_args()

    report = audit(args.db, args.themes)
    print_summary(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))
        print(f"report: {args.out}")

    if args.strict and (report["suspected_corrupt_seeds"] or any(report["integrity"].values())):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
