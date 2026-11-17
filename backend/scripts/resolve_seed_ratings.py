"""Resolve harvested Letterboxd films to exact TMDB ids.

Resolution order (cheapest and most reliable first):

1. Exact normalized title + release year against the existing ``movie_cache``.
2. TMDB ``/search/movie`` with the year, then without it as a fallback.
   A candidate is accepted only on an exact normalized title match; duplicate
   exact matches require a 2x popularity margin, otherwise they stay unresolved.
3. For still-unresolved films: read the IMDb id from the Letterboxd film page
   and resolve it through TMDB ``/find`` (exact external-id lookup).

Every result is checkpointed per film in ``seeds/resolution.db`` so the run is
resumable and restart-safe. Unresolved candidates are written to
``seeds/unresolved.csv`` for review. The script never writes to the app DB.

Usage:
    cd backend && ../.venv/bin/python scripts/resolve_seed_ratings.py
    ... --limit 20                 # smoke test
    ... --imdb-only                # retry the unresolved queue only
    ... --retry-unresolved         # rerun title/year search for unresolved
"""

from __future__ import annotations

import argparse
import csv
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
sys.path.insert(0, str(BACKEND))

from services import tmdb  # noqa: E402
from letterboxd_common import (  # noqa: E402
    BASE,
    ChallengeError,
    extract_imdb_id,
    fetch,
    is_challenge,
    response_html,
    response_status,
    write_json,
)

DEFAULT_SEEDS = BACKEND / "letterboxd_data" / "seeds"
DEFAULT_DB = BACKEND / "cinematch.db"


def exact_title(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_title(value: str | None) -> str:
    """Loose title key: punctuation/accents and a leading article are ignored."""
    return re.sub(r"^(the|a|an) ", "", exact_title(value))


def film_key(slug: str, year: int | None) -> str:
    return f"{slug}|{year if year is not None else ''}"


def load_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text())
    except (OSError, ValueError):
        return {}


def load_harvest(seeds_dir: Path, min_ratings: int) -> tuple[dict, Counter, list[dict]]:
    """Return unique films, like counts, and valid profile states.

    A profile is valid when it has at least ``min_ratings`` star ratings, at
    least 100 watched entries, and at least three distinct rating values.
    Profiles with zero ratings are excluded (they are not usable as CF seeds).
    """
    films: dict[str, dict] = {}
    likes: Counter = Counter()
    profiles: list[dict] = []

    for state_path in sorted((seeds_dir / "state").glob("*.done.json")):
        state = load_state(state_path)
        pseudo = state.get("pseudonym")
        if not pseudo:
            continue
        raw_path = seeds_dir / "raw" / f"{pseudo}.jsonl"
        likes_path = seeds_dir / "likes" / f"{pseudo}.jsonl"
        if not raw_path.exists():
            continue

        rows = []
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        liked = []
        if likes_path.exists():
            for line in likes_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    liked.append(json.loads(line))

        rated = [r for r in rows if r.get("rating") is not None]
        distinct = len({r["rating"] for r in rated})
        if len(rated) < min_ratings or len(rows) < 100 or distinct < 3:
            continue

        profiles.append(
            {
                "pseudonym": pseudo,
                "source": state.get("source", "file"),
                "state": state,
                "rows": rows,
                "liked": liked,
            }
        )

        for row in rows:
            key = film_key(row["slug"], row.get("year"))
            film = films.setdefault(
                key,
                {
                    "key": key,
                    "slug": row["slug"],
                    "year": row.get("year"),
                    "title": row.get("title") or "",
                    "raters": set(),
                    "ratings": [],
                    "watchers": set(),
                },
            )
            film["watchers"].add(pseudo)
            if row.get("rating") is not None:
                film["raters"].add(pseudo)
                film["ratings"].append(float(row["rating"]))
        for row in liked:
            likes[film_key(row["slug"], row.get("year"))] += 1

    return films, likes, profiles


def load_mustafa_title_year(db_path: Path) -> set[tuple[str, int | None]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        user = con.execute(
            "select user_id from users where username = 'mustafa'"
        ).fetchone()
        if not user:
            return set()
        rows = con.execute(
            """
            select m.title, m.release_year
            from logged_movies l
            join movie_cache m on m.tmdb_id = l.tmdb_id
            where l.user_id = ?
            """,
            (user[0],),
        ).fetchall()
        return {(normalize_title(title), year) for title, year in rows}
    finally:
        con.close()


def select_import_set(
    films: dict[str, dict],
    likes: Counter,
    mustafa_keys: set[tuple[str, int | None]],
) -> dict[str, dict]:
    """Films worth resolving: co-rated, co-liked, or a mustafa overlap.

    Singletons that no second seed rated are only kept when they are also liked
    by at least two seeds or match a film mustafa already rated. This keeps the
    catalog focused while preserving every CF edge and the overlap target.
    """
    selected = {}
    for key, film in films.items():
        title_key = (normalize_title(film["title"]), film["year"])
        if (
            len(film["raters"]) >= 2
            or likes.get(key, 0) >= 2
            or title_key in mustafa_keys
        ):
            selected[key] = film
    return selected


def load_catalog(db_path: Path) -> dict[tuple[str, int | None], list[dict]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        catalog: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
        for row in con.execute(
            """
            select tmdb_id, title, release_year, overview, genres, poster_path,
                   popularity, vote_average
            from movie_cache
            """
        ):
            tmdb_id, title, year, overview, genres, poster, popularity, vote = row
            if not title:
                continue
            catalog[(normalize_title(title), year)].append(
                {
                    "tmdb_id": int(tmdb_id),
                    "title": title,
                    "overview": overview or "",
                    "genres": [g for g in (genres or "").split(",") if g],
                    "poster_path": poster,
                    "release_year": year,
                    "popularity": popularity or 0.0,
                    "vote_average": vote or 0.0,
                }
            )
        return catalog
    finally:
        con.close()


def init_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(
        """
        create table if not exists resolution (
            film_key text primary key,
            slug text not null,
            year integer,
            title text not null,
            status text not null,
            method text,
            tmdb_id integer,
            reason text,
            candidates text,
            payload text,
            updated_at text not null
        )
        """
    )
    con.execute(
        "create index if not exists ix_resolution_status on resolution(status)"
    )
    con.commit()
    return con


def load_cache(con: sqlite3.Connection) -> dict[str, dict]:
    rows = con.execute(
        "select film_key, status, tmdb_id, reason from resolution"
    ).fetchall()
    return {
        key: {"status": status, "tmdb_id": tmdb_id, "reason": reason}
        for key, status, tmdb_id, reason in rows
    }


def store(
    con: sqlite3.Connection,
    film: dict,
    status: str,
    *,
    method: str | None = None,
    tmdb_id: int | None = None,
    reason: str | None = None,
    candidates: list[dict] | None = None,
    payload: dict | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        """
        insert into resolution
            (film_key, slug, year, title, status, method, tmdb_id, reason,
             candidates, payload, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(film_key) do update set
            slug = excluded.slug,
            year = excluded.year,
            title = excluded.title,
            status = excluded.status,
            method = excluded.method,
            tmdb_id = excluded.tmdb_id,
            reason = excluded.reason,
            candidates = excluded.candidates,
            payload = excluded.payload,
            updated_at = excluded.updated_at
        """,
        (
            film["key"],
            film["slug"],
            film.get("year"),
            film.get("title") or "",
            status,
            method,
            tmdb_id,
            reason,
            json.dumps(candidates or [], ensure_ascii=False),
            json.dumps(payload or {}, ensure_ascii=False) if payload else None,
            now,
        ),
    )
    con.commit()


def _dedupe(candidates: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for candidate in candidates:
        tmdb_id = candidate.get("tmdb_id")
        if tmdb_id and tmdb_id not in seen:
            seen.add(tmdb_id)
            out.append(candidate)
    return out


def _margin(candidates: list[dict], method: str):
    candidates = _dedupe(candidates)
    if len(candidates) == 1:
        return candidates[0], method
    ranked = sorted(
        candidates,
        key=lambda m: (m.get("popularity") or 0.0, m.get("vote_average") or 0.0),
        reverse=True,
    )
    top, second = ranked[0], ranked[1]
    top_pop = float(top.get("popularity") or 0.0)
    second_pop = float(second.get("popularity") or 0.0)
    if top_pop > 0 and top_pop >= max(second_pop, 0.1) * 2.0:
        return top, f"{method}_margin"
    return None, "ambiguous"


def choose_match(candidates: list[dict], title: str, year: int | None):
    """Return (movie, method) or (None, reason).

    Exact titles are compared with leading articles preserved. A loose match
    (one title has "The"/"A") is accepted only in the same release year: an
    article difference plus a ±1 year is how "ATM" (2017) could otherwise be
    mistaken for the unrelated "The ATM" (2016).
    """
    candidates = _dedupe(candidates)
    if not candidates:
        return None, "no_results"

    wanted = exact_title(title)
    exact = [c for c in candidates if exact_title(c.get("title")) == wanted]

    if exact:
        if year is None:
            if len(exact) == 1:
                return exact[0], "title_only"
            movie, method = _margin(exact, "title_only")
            return (movie, method) if movie else (None, "ambiguous_title")

        same_year = [c for c in exact if c.get("release_year") == year]
        if len(same_year) == 1:
            return same_year[0], "title_year"
        if len(same_year) > 1:
            movie, method = _margin(same_year, "title_year")
            return (movie, method) if movie else (None, "ambiguous_year")

        near = [
            c
            for c in exact
            if c.get("release_year") is not None
            and abs(int(c["release_year"]) - int(year)) <= 1
        ]
        if len(near) == 1:
            return near[0], "title_year_pm1"
        if len(near) > 1:
            movie, method = _margin(near, "title_year_pm1")
            return (movie, method) if movie else (None, "ambiguous_year_pm1")

    # Same-year article variants are safe enough to accept; off-by-one article
    # variants are not (see the ATM example above).
    loose_wanted = normalize_title(title)
    loose = [c for c in candidates if normalize_title(c.get("title")) == loose_wanted]
    if year is not None:
        loose_same_year = [c for c in loose if c.get("release_year") == year]
        if len(loose_same_year) == 1:
            return loose_same_year[0], "title_year_article"
        if len(loose_same_year) > 1:
            movie, method = _margin(loose_same_year, "title_year_article")
            return (movie, method) if movie else (None, "ambiguous_year_article")
        if loose_same_year:
            return None, "ambiguous_year_article"
    return None, "no_exact_title" if not exact else "year_mismatch"


def candidate_summary(candidates: list[dict], limit: int = 8) -> list[dict]:
    return [
        {
            "tmdb_id": c.get("tmdb_id"),
            "title": c.get("title"),
            "year": c.get("release_year"),
            "popularity": c.get("popularity"),
        }
        for c in _dedupe(candidates)[:limit]
    ]


def resolve_one(film: dict) -> dict:
    """Search TMDB for one film. Pure/network-only; callers own persistence."""
    title = film.get("title") or ""
    year = film.get("year")
    if not title:
        return {"film": film, "status": "unresolved", "reason": "missing_title"}

    attempts = [year, None] if year is not None else [None]
    collected: list[dict] = []
    last_reason = "no_results"
    for search_year in attempts:
        try:
            candidates = tmdb.search_movies(title, year=search_year, limit=10)
        except Exception as exc:  # noqa: BLE001 — one film must not kill the run
            return {
                "film": film,
                "status": "error",
                "reason": f"tmdb_error: {exc!r}",
                "candidates": collected,
            }
        collected.extend(candidates)
        movie, method = choose_match(candidates, title, year)
        if movie:
            return {
                "film": film,
                "status": "resolved_search",
                "method": method,
                "tmdb_id": movie["tmdb_id"],
                "payload": movie,
                "candidates": collected,
            }
        last_reason = method
        # If the year-filtered search already found the exact title but the
        # year was wrong, an unfiltered retry can find a ±1 release-year match.
        if year is not None and search_year is not None and method == "ambiguous_year":
            continue

    return {
        "film": film,
        "status": "unresolved",
        "reason": last_reason,
        "candidates": collected,
    }


def fetch_imdb_id(slug: str) -> tuple[str | None, str | None]:
    """Return (imdb_id, error_reason) from the Letterboxd film page."""
    try:
        response = fetch(f"{BASE}/film/{slug}/")
    except Exception as exc:  # noqa: BLE001
        return None, f"letterboxd_error: {exc!r}"
    if is_challenge(response):
        raise ChallengeError(f"anti-bot page on film {slug}")
    status = response_status(response)
    if status != 200:
        return None, f"letterboxd_http_{status}"
    imdb_id = extract_imdb_id(response_html(response))
    if not imdb_id:
        return None, "no_imdb_link"
    return imdb_id, None


def imdb_fallback(
    con: sqlite3.Connection,
    films: dict[str, dict],
    cache: dict[str, dict],
    *,
    only_keys: set[str] | None = None,
    limit: int = 0,
) -> tuple[int, int]:
    """Resolve cached unresolved films via Letterboxd -> IMDb -> TMDB /find.

    ``only_keys`` limits the pass to films that just failed title/year search;
    ``None`` means every currently-unresolved cached film (``--imdb-only``).
    """
    pending = []
    for key, row in cache.items():
        if row["status"] != "unresolved":
            continue
        if only_keys is not None and key not in only_keys:
            continue
        film = films.get(key)
        if not film:
            continue
        pending.append(film)
    pending.sort(key=lambda f: f["key"])
    if limit:
        pending = pending[:limit]

    resolved = failed = 0
    for index, film in enumerate(pending, 1):
        try:
            imdb_id, error = fetch_imdb_id(film["slug"])
        except ChallengeError as exc:
            challenge_path = DEFAULT_SEEDS / "state" / "resolution_challenge.json"
            write_json(challenge_path, {"film": film["slug"], "error": str(exc)})
            print(f"STOP: {exc} (saved {challenge_path})")
            break
        if error:
            store(con, film, "unresolved", reason=error)
            failed += 1
            continue
        try:
            movie = tmdb.find_by_imdb(imdb_id)
        except Exception as exc:  # noqa: BLE001
            store(con, film, "error", reason=f"tmdb_find_error: {exc!r}")
            failed += 1
            continue
        if not movie:
            store(con, film, "unresolved", reason="imdb_not_in_tmdb")
            failed += 1
            continue
        store(
            con,
            film,
            "resolved_imdb",
            method="imdb_find",
            tmdb_id=movie["tmdb_id"],
            payload=movie,
        )
        cache[film["key"]] = {
            "status": "resolved_imdb",
            "tmdb_id": movie["tmdb_id"],
            "reason": None,
        }
        resolved += 1
        if index % 25 == 0:
            print(f"  imdb fallback {index}/{len(pending)}: resolved={resolved} failed={failed}")
    return resolved, failed


def write_unresolved_csv(
    path: Path,
    con: sqlite3.Connection,
    films: dict[str, dict],
    likes: Counter,
) -> int:
    rows = con.execute(
        """
        select film_key, slug, year, title, status, reason, candidates
        from resolution
        where status in ('unresolved', 'error')
        order by title collate nocase
        """
    ).fetchall()
    temp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "film_key",
                "slug",
                "year",
                "title",
                "status",
                "reason",
                "ratings",
                "likes",
                "candidates",
            ]
        )
        for key, slug, year, title, status, reason, candidates in rows:
            film = films.get(key, {})
            parsed = json.loads(candidates or "[]")
            compact = "; ".join(
                f"{c.get('tmdb_id')}:{c.get('title')} ({c.get('year')})"
                for c in parsed[:6]
            )
            writer.writerow(
                [
                    key,
                    slug,
                    year or "",
                    title,
                    status,
                    reason or "",
                    len(film.get("ratings") or []),
                    likes.get(key, 0),
                    compact,
                ]
            )
    os.replace(temp, path)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds-dir", type=Path, default=DEFAULT_SEEDS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--resolution-db", type=Path, default=DEFAULT_SEEDS / "resolution.db"
    )
    parser.add_argument("--min-ratings", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rps", type=float, default=8.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument("--imdb-fallback", action="store_true", default=True)
    parser.add_argument("--skip-imdb-fallback", dest="imdb_fallback", action="store_false")
    parser.add_argument("--imdb-only", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    films, likes, profiles = load_harvest(args.seeds_dir, args.min_ratings)
    mustafa_keys = load_mustafa_title_year(args.db)
    selected = select_import_set(films, likes, mustafa_keys)
    catalog = load_catalog(args.db)
    con = init_cache(args.resolution_db)
    cache = load_cache(con)

    print(
        f"profiles={len(profiles)} harvest_films={len(films)} "
        f"selected={len(selected)} existing_resolution_rows={len(cache)}"
    )

    counts: Counter = Counter()
    pending: list[dict] = []
    newly_unresolved: set[str] = set()
    skipped = 0

    if not args.imdb_only:
        for key, film in selected.items():
            previous = cache.get(key)
            if previous and not args.force:
                if previous["status"].startswith("resolved") and previous["tmdb_id"]:
                    skipped += 1
                    continue
                if previous["status"] == "unresolved" and not args.retry_unresolved:
                    skipped += 1
                    continue
            exact = catalog.get((normalize_title(film["title"]), film["year"]), [])
            if len(exact) == 1:
                movie = exact[0]
                store(
                    con,
                    film,
                    "resolved_catalog",
                    method="catalog_title_year",
                    tmdb_id=movie["tmdb_id"],
                    payload=movie,
                )
                cache[key] = {
                    "status": "resolved_catalog",
                    "tmdb_id": movie["tmdb_id"],
                    "reason": None,
                }
                counts["resolved_catalog"] += 1
            elif len(exact) > 1:
                pending.append(film)
            else:
                pending.append(film)

        pending.sort(key=lambda f: f["key"])
        if args.limit:
            pending = pending[: args.limit]
        print(
            f"catalog_resolved={counts['resolved_catalog']} "
            f"pending_search={len(pending)} skipped_existing={skipped}"
        )

        tmdb.configure_rate_limit(args.rps)
        completed = errors = 0
        search_started = time.monotonic()
        if pending:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                futures = {pool.submit(resolve_one, film): film for film in pending}
                for future in as_completed(futures):
                    film = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "film": film,
                            "status": "error",
                            "reason": f"worker_error: {exc!r}",
                            "candidates": [],
                        }
                    status = result["status"]
                    if status.startswith("resolved"):
                        store(
                            con,
                            film,
                            status,
                            method=result.get("method"),
                            tmdb_id=result.get("tmdb_id"),
                            payload=result.get("payload"),
                            candidates=result.get("candidates"),
                        )
                        counts[status] += 1
                        cache[film["key"]] = {
                            "status": status,
                            "tmdb_id": result.get("tmdb_id"),
                            "reason": None,
                        }
                    else:
                        store(
                            con,
                            film,
                            status,
                            reason=result.get("reason"),
                            candidates=result.get("candidates"),
                        )
                        counts[status] += 1
                        cache[film["key"]] = {
                            "status": status,
                            "tmdb_id": None,
                            "reason": result.get("reason"),
                        }
                        if status == "unresolved":
                            newly_unresolved.add(film["key"])
                    completed += 1
                    if status == "error":
                        errors += 1
                    if completed % 100 == 0 or completed == len(pending):
                        elapsed = max(0.001, time.monotonic() - search_started)
                        rate = completed / elapsed
                        print(
                            f"  search {completed}/{len(pending)} "
                            f"resolved={counts['resolved_search']} "
                            f"unresolved={counts['unresolved']} errors={errors} "
                            f"rate={rate:.1f}/s"
                        )

    # IMDb fallback is deliberately serial: Letterboxd pacing is the hard
    # anti-abuse constraint, while TMDB search above can run in parallel.
    if args.imdb_fallback:
        if args.imdb_only:
            only_keys = None
        else:
            only_keys = set(newly_unresolved)
            if args.retry_unresolved:
                only_keys |= {
                    key for key, row in cache.items() if row["status"] == "unresolved"
                }
        target_count = (
            sum(1 for row in cache.values() if row["status"] == "unresolved")
            if only_keys is None
            else len(only_keys)
        )
        if target_count:
            print(f"IMDb fallback for {target_count} unresolved films")
            resolved, failed = imdb_fallback(
                con,
                films,
                cache,
                only_keys=only_keys,
                limit=args.limit,
            )
            counts["resolved_imdb"] += resolved
            counts["imdb_failed"] += failed

    unresolved = write_unresolved_csv(
        args.seeds_dir / "unresolved.csv", con, films, likes
    )
    totals = Counter()
    for row in cache.values():
        totals[row["status"]] += 1

    summary = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "seeds_dir": str(args.seeds_dir),
        "db": str(args.db),
        "resolution_db": str(args.resolution_db),
        "profiles": len(profiles),
        "harvest_films": len(films),
        "selected_films": len(selected),
        "this_run": dict(counts),
        "cache_totals": dict(totals),
        "unresolved_or_error_rows_written": unresolved,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    write_json(args.seeds_dir / "resolution_summary.json", summary)
    print(json.dumps(summary, indent=2))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
