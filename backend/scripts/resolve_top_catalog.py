"""Resolve the Letterboxd all-time popular list to exact TMDB ids.

The popularity harvest stores Letterboxd slugs plus title/year.  This script
turns that list into a catalog plan without writing the live app database:

1. exact slug match against ``movie_cache``;
2. exact normalized title/year match against ``movie_cache``;
3. reuse the existing seed resolver cache;
4. TMDB title/year search with the same conservative acceptance rules as the
   seed resolver;
5. last resort: read the film page's ``data-tmdb-id`` directly (this also
   captures Letterboxd themes for that film).

Every outcome is checkpointed in ``top_logged/resolution.db``.  Unresolved
rows are never guessed.

Usage:
    cd backend && ../.venv/bin/python scripts/resolve_top_catalog.py
    ... --limit 500 --workers 4
    ... --skip-letterboxd-fallback
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
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))

from bs4 import BeautifulSoup  # noqa: E402

from letterboxd_common import (  # noqa: E402
    BASE,
    ChallengeError,
    fetch,
    is_challenge,
    response_html,
    response_status,
)
from services import tmdb  # noqa: E402

DEFAULT_TOP_DIR = BACKEND / "letterboxd_data" / "top_logged"
DEFAULT_DB = BACKEND / "cinematch.db"
SEED_RESOLUTION = BACKEND / "letterboxd_data" / "seeds" / "resolution.db"
THEME_LINK_RE = re.compile(r"^/films/(theme|mini-theme)/([^/]+)/")


def exact_title(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_title(value: str | None) -> str:
    return re.sub(r"^(the|a|an) ", "", exact_title(value))


def film_key(slug: str, year: int | None) -> str:
    return f"{slug}|{year if year is not None else ''}"


def load_top_rows(top_dir: Path) -> list[dict[str, Any]]:
    combined = top_dir / "top_20000.jsonl"
    if combined.exists():
        rows = []
        for line in combined.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    rows = []
    for path in sorted((top_dir / "pages").glob("page_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(payload.get("films") or [])
    rows.sort(key=lambda row: (row.get("rank") or 10**9, row.get("slug") or ""))
    return rows


def load_catalog(db_path: Path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    by_slug: dict[str, list[dict]] = defaultdict(list)
    by_title_year: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
    for row in con.execute(
        "select tmdb_id, title, release_year, popularity, vote_average, letterboxd_slug from movie_cache"
    ):
        item = dict(row)
        if item.get("letterboxd_slug"):
            by_slug[item["letterboxd_slug"]].append(item)
        by_title_year[
            (normalize_title(item.get("title")), item.get("release_year"))
        ].append(item)
    con.close()
    return by_slug, by_title_year


def load_seed_resolution(path: Path) -> tuple[dict[str, dict], dict[tuple[str, int | None], dict]]:
    if not path.exists():
        return {}, {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    by_slug = {}
    by_title_year = {}
    for row in con.execute(
        "select film_key, slug, year, title, status, method, tmdb_id, reason, candidates, payload "
        "from resolution where tmdb_id is not null and status like 'resolved%'"
    ):
        item = dict(row)
        by_slug.setdefault(item["slug"], item)
        by_title_year.setdefault(
            (normalize_title(item.get("title")), item.get("year")), item
        )
    con.close()
    return by_slug, by_title_year


def init_resolution(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(
        """
        create table if not exists resolution (
            film_key text primary key,
            rank integer,
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
    con.execute("create index if not exists ix_top_status on resolution(status)")
    con.execute("create index if not exists ix_top_tmdb on resolution(tmdb_id)")
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


def store(con, film, status, *, method=None, tmdb_id=None, reason=None,
          candidates=None, payload=None):
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        """
        insert into resolution
            (film_key, rank, slug, year, title, status, method, tmdb_id,
             reason, candidates, payload, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(film_key) do update set
            rank = excluded.rank,
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
            film.get("rank"),
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
    candidates = _dedupe(candidates)
    if not candidates:
        return None, "no_results"

    wanted = exact_title(title)
    exact = [c for c in candidates if exact_title(c.get("title")) == wanted]
    if exact:
        if year is None:
            if len(exact) == 1:
                return exact[0], "title_only"
            return _margin(exact, "title_only")
        same_year = [c for c in exact if c.get("release_year") == year]
        if len(same_year) == 1:
            return same_year[0], "title_year"
        if len(same_year) > 1:
            return _margin(same_year, "title_year")
        near = [
            c for c in exact
            if c.get("release_year") is not None
            and abs(int(c["release_year"]) - int(year)) <= 1
        ]
        if len(near) == 1:
            return near[0], "title_year_pm1"
        if len(near) > 1:
            return _margin(near, "title_year_pm1")

    loose_wanted = normalize_title(title)
    loose = [c for c in candidates if normalize_title(c.get("title")) == loose_wanted]
    if year is not None:
        loose_same_year = [c for c in loose if c.get("release_year") == year]
        if len(loose_same_year) == 1:
            return loose_same_year[0], "title_year_article"
        if len(loose_same_year) > 1:
            return _margin(loose_same_year, "title_year_article")
        if loose_same_year:
            return None, "ambiguous_year_article"
    return None, "no_exact_title" if not exact else "year_mismatch"


def choose_catalog(catalog_matches: list[dict], title: str, year: int | None):
    if not catalog_matches:
        return None, "no_catalog_match"
    if len(catalog_matches) == 1:
        return catalog_matches[0], "catalog_title_year"
    ranked = sorted(
        catalog_matches,
        key=lambda m: (m.get("popularity") or 0.0, m.get("vote_average") or 0.0),
        reverse=True,
    )
    top, second = ranked[0], ranked[1]
    top_pop = float(top.get("popularity") or 0.0)
    second_pop = float(second.get("popularity") or 0.0)
    if top_pop > 0 and top_pop >= max(second_pop, 0.1) * 2.0:
        return top, "catalog_title_year_margin"
    return None, "ambiguous_catalog"


def fetch_letterboxd_film(slug: str) -> dict[str, Any] | None:
    """Read the exact TMDB id and Letterboxd themes from one film page."""
    response = fetch(f"{BASE}/film/{slug}/", timeout=45)
    if is_challenge(response):
        raise ChallengeError(f"anti-bot page on film {slug}")
    if response_status(response) != 200:
        return None
    html = response_html(response)
    soup = BeautifulSoup(html, "lxml")

    tmdb_id = None
    body = soup.find("body")
    if body is not None:
        raw = (body.get("data-tmdb-id") or "").strip()
        if raw.isdigit():
            tmdb_id = int(raw)
    if tmdb_id is None:
        match = re.search(r"themoviedb\.org/movie/(\d+)", html)
        if match:
            tmdb_id = int(match.group(1))

    themes = []
    seen = set()
    for anchor in soup.select('a[href*="/films/theme/"], a[href*="/films/mini-theme/"]'):
        href = anchor.get("href") or ""
        match = THEME_LINK_RE.match(href)
        if not match:
            continue
        kind, theme_slug = match.group(1), match.group(2)
        label = anchor.get_text(" ", strip=True)
        if not label:
            continue
        key = (kind, theme_slug)
        if key in seen:
            continue
        seen.add(key)
        themes.append(
            {
                "kind": kind,
                "slug": theme_slug,
                "label": label,
                "source": "letterboxd",
            }
        )

    rating_count = None
    text = html
    count_match = re.search(r"Ratings\s+([\d,.]+[KM]?)\s+fans", text, re.IGNORECASE)
    if count_match:
        raw = count_match.group(1).replace(",", "").upper()
        try:
            if raw.endswith("K"):
                rating_count = int(float(raw[:-1]) * 1_000)
            elif raw.endswith("M"):
                rating_count = int(float(raw[:-1]) * 1_000_000)
            else:
                rating_count = int(float(raw))
        except ValueError:
            rating_count = None

    title = None
    year = None
    title_tag = soup.find("title")
    if title_tag:
        match = re.search(r"(.+?)\s+\((\d{4})\)", title_tag.get_text(" ", strip=True))
        if match:
            title = match.group(1).strip()
            year = int(match.group(2))

    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "slug": slug,
        "themes": themes,
        "rating_count": rating_count,
        "source": "letterboxd_film_page",
    }


def resolve_one(film: dict, catalog_by_slug, catalog_by_title_year,
                seed_by_slug, seed_by_title_year, *, allow_search: bool = True) -> dict:
    slug = film["slug"]
    title = film.get("title") or ""
    year = film.get("year")

    matches = catalog_by_slug.get(slug) or []
    if matches:
        movie = matches[0]
        return {
            "status": "resolved_catalog",
            "method": "catalog_slug",
            "tmdb_id": int(movie["tmdb_id"]),
            "payload": movie,
        }
    movie, method = choose_catalog(
        catalog_by_title_year.get((normalize_title(title), year), []), title, year
    )
    if movie:
        return {
            "status": "resolved_catalog",
            "method": method,
            "tmdb_id": int(movie["tmdb_id"]),
            "payload": movie,
        }

    seed = seed_by_slug.get(slug)
    if not seed:
        seed = seed_by_title_year.get((normalize_title(title), year))
    if seed and seed.get("tmdb_id"):
        payload = {}
        try:
            payload = json.loads(seed.get("payload") or "{}")
        except ValueError:
            payload = {}
        return {
            "status": "resolved_seed_cache",
            "method": f"seed_{seed.get('method') or 'cache'}",
            "tmdb_id": int(seed["tmdb_id"]),
            "payload": payload,
        }

    if not allow_search:
        return {"status": "needs_network", "reason": "no_deterministic_match"}

    attempts = [year, None] if year is not None else [None]
    collected = []
    last_reason = "no_results"
    for search_year in attempts:
        try:
            candidates = tmdb.search_movies(title, year=search_year, limit=10)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "reason": f"tmdb_error: {exc!r}",
                "candidates": collected,
            }
        collected.extend(candidates)
        movie, method = choose_match(candidates, title, year)
        if movie:
            return {
                "status": "resolved_search",
                "method": method,
                "tmdb_id": int(movie["tmdb_id"]),
                "payload": movie,
                "candidates": collected,
            }
        last_reason = method
        if year is not None and search_year is not None and method == "ambiguous_year":
            continue
    return {
        "status": "unresolved",
        "reason": last_reason,
        "candidates": collected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-dir", type=Path, default=DEFAULT_TOP_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--resolution-db", type=Path, default=DEFAULT_TOP_DIR / "resolution.db")
    parser.add_argument("--seed-resolution", type=Path, default=SEED_RESOLUTION)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--rps", type=float, default=8.0)
    parser.add_argument("--retry-unresolved", action="store_true")
    parser.add_argument("--skip-letterboxd-fallback", action="store_true")
    parser.add_argument("--letterboxd-limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    started = time.monotonic()
    top_dir = args.top_dir.resolve()
    rows = load_top_rows(top_dir)
    if not rows:
        print(f"no popularity rows in {top_dir}", file=sys.stderr)
        return 2
    if args.limit:
        rows = rows[: args.limit]

    catalog_by_slug, catalog_by_title_year = load_catalog(args.db.resolve())
    seed_by_slug, seed_by_title_year = load_seed_resolution(args.seed_resolution)
    con = init_resolution(args.resolution_db)
    cache = load_cache(con)

    print(
        json.dumps(
            {
                "top_rows": len(rows),
                "catalog_slugs": len(catalog_by_slug),
                "seed_resolutions": len(seed_by_slug),
                "cached_resolutions": len(cache),
                "workers": args.workers,
            },
            indent=2,
        )
    )

    films = []
    for row in rows:
        films.append(
            {
                **row,
                "key": film_key(row["slug"], row.get("year")),
            }
        )

    # Cheap deterministic matches first; TMDB only for the remainder.
    stats = Counter()
    pending = []
    for film in films:
        cached = cache.get(film["key"])
        if cached and cached.get("status") != "unresolved":
            stats[f"cached_{cached['status']}"] += 1
            continue
        if cached and cached.get("status") == "unresolved" and not args.retry_unresolved:
            stats["cached_unresolved"] += 1
            continue
        result = resolve_one(
            film, catalog_by_slug, catalog_by_title_year,
            seed_by_slug, seed_by_title_year, allow_search=False,
        )
        if result.get("tmdb_id"):
            store(
                con, film, result["status"],
                method=result.get("method"),
                tmdb_id=result["tmdb_id"],
                payload=result.get("payload"),
                candidates=result.get("candidates"),
            )
            stats[result["status"]] += 1
        else:
            pending.append((film, result))
    con.commit()
    print(f"deterministic pass: {dict(stats)}", flush=True)

    # Network search for anything still unresolved.
    tmdb.configure_rate_limit(args.rps)
    search_stats = Counter()
    letterboxd_queue = []
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(
                    resolve_one, film, catalog_by_slug, catalog_by_title_year,
                    seed_by_slug, seed_by_title_year,
                ): (film, prior)
                for film, prior in pending
            }
            for index, future in enumerate(as_completed(futures), 1):
                film, prior = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "error", "reason": f"resolver_error: {exc!r}"}
                if result.get("tmdb_id"):
                    store(
                        con, film, result["status"],
                        method=result.get("method"),
                        tmdb_id=result["tmdb_id"],
                        payload=result.get("payload"),
                        candidates=result.get("candidates"),
                    )
                    if result["status"].startswith("resolved_search"):
                        search_stats[result["method"]] += 1
                elif args.skip_letterboxd_fallback:
                    store(
                        con, film, "unresolved",
                        reason=result.get("reason"),
                        candidates=result.get("candidates"),
                    )
                    search_stats["unresolved"] += 1
                else:
                    letterboxd_queue.append((film, result))
                if index % 100 == 0 or index == len(futures):
                    print(
                        f"  search {index}/{len(futures)} "
                        f"resolved={sum(search_stats.values())} "
                        f"fallback={len(letterboxd_queue)}",
                        flush=True,
                    )

    # Last resort: read the TMDB id and themes directly from Letterboxd.
    letterboxd_stats = Counter()
    if letterboxd_queue and not args.skip_letterboxd_fallback:
        if args.letterboxd_limit:
            letterboxd_queue = letterboxd_queue[: args.letterboxd_limit]
        for index, (film, prior) in enumerate(letterboxd_queue, 1):
            try:
                page = fetch_letterboxd_film(film["slug"])
            except ChallengeError as exc:
                store(con, film, "challenge", reason=str(exc))
                print(f"CHALLENGE: {exc}", file=sys.stderr)
                break
            except Exception as exc:  # noqa: BLE001
                store(con, film, "error", reason=f"letterboxd_error: {exc!r}")
                letterboxd_stats["error"] += 1
                continue
            if not page or not page.get("tmdb_id"):
                store(
                    con, film, "unresolved",
                    reason=(prior or {}).get("reason") or "no_tmdb_id_on_letterboxd",
                    candidates=(prior or {}).get("candidates"),
                )
                letterboxd_stats["unresolved"] += 1
            else:
                store(
                    con, film, "resolved_letterboxd",
                    method="letterboxd_tmdb_id",
                    tmdb_id=page["tmdb_id"],
                    payload=page,
                )
                letterboxd_stats["resolved"] += 1
            if index % 25 == 0 or index == len(letterboxd_queue):
                print(
                    f"  letterboxd {index}/{len(letterboxd_queue)} "
                    f"resolved={letterboxd_stats['resolved']} "
                    f"unresolved={letterboxd_stats['unresolved']}",
                    flush=True,
                )

    # Final report + portable resolved JSONL.
    out_path = args.out or (top_dir / "resolved.jsonl")
    temp = out_path.with_suffix(out_path.suffix + ".tmp")
    counts = Counter()
    with temp.open("w", encoding="utf-8") as handle:
        for row in con.execute(
            "select rank, slug, year, title, status, method, tmdb_id, reason, candidates, payload "
            "from resolution order by rank, slug"
        ):
            rank, slug, year, title, status, method, tmdb_id, reason, candidates, payload = row
            counts[status] += 1
            handle.write(
                json.dumps(
                    {
                        "rank": rank,
                        "slug": slug,
                        "year": year,
                        "title": title,
                        "status": status,
                        "method": method,
                        "tmdb_id": tmdb_id,
                        "reason": reason,
                        "candidates": json.loads(candidates or "[]"),
                        "payload": json.loads(payload or "{}") if payload else {},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    os.replace(temp, out_path)
    elapsed = round(time.monotonic() - started, 1)
    summary = {
        "top_rows": len(rows),
        "resolved": sum(v for k, v in counts.items() if k.startswith("resolved")),
        "unresolved": counts.get("unresolved", 0),
        "errors": counts.get("error", 0) + counts.get("challenge", 0),
        "by_status": dict(counts),
        "search_methods": dict(search_stats),
        "letterboxd_fallback": dict(letterboxd_stats),
        "elapsed_seconds": elapsed,
        "output": str(out_path),
    }
    (top_dir / "resolution_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
