"""Harvest full public Letterboxd histories (rated + unrated) and likes.

Usage:
    cd backend && ../.venv/bin/python scripts/scrape_letterboxd_profiles.py \
        --handles-file letterboxd_data/seeds/handles.json \
        --out-dir letterboxd_data/seeds

Dry run on one profile:
    ... --handle thomasflight --limit 1

Outputs (per pseudonym, never the raw handle):
    raw/<pseudonym>.jsonl        every watched film (rating may be null)
    likes/<pseudonym>.jsonl      separately liked films
    state/<pseudonym>.done.json  counts + validation flags
    state/challenge.json         written and exit 3 on an anti-bot page

Resumable: a completed profile is skipped on the next run. Requests are
serialized at 1.2–2.0 s via letterboxd_common.fetch.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from letterboxd_common import (
    BASE,
    ChallengeError,
    fetch,
    is_challenge,
    load_json,
    next_page_url,
    parse_grid,
    parse_profile_films,
    pseudonym,
    response_html,
    response_status,
    write_json,
    write_jsonl,
)

STARS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]


def scrape_listing(start_url: str, max_items: int, max_pages: int) -> tuple[list[dict], int]:
    rows: dict[str, dict] = {}
    visited: set[str] = set()
    url = start_url
    pages = 0
    while url and url not in visited and pages < max_pages and len(rows) < max_items:
        visited.add(url)
        response = fetch(url)
        if is_challenge(response):
            raise ChallengeError(f"anti-bot page at {url}")
        if response_status(response) != 200:
            break
        page_html = response_html(response)
        pages += 1
        for row in parse_grid(page_html):
            previous = rows.get(row["slug"])
            if previous is None or (
                previous.get("rating") is None and row.get("rating") is not None
            ):
                rows[row["slug"]] = row
        if len(rows) >= max_items:
            break
        url = next_page_url(page_html, url)
    return list(rows.values())[:max_items], pages


def rating_histogram(rows: list[dict]) -> dict:
    hist = Counter(row.get("rating") for row in rows if row.get("rating") is not None)
    return {str(star): hist.get(star, 0) for star in STARS}


def validate_films(rows: list[dict], expected: int | None) -> dict:
    rated = [row for row in rows if row.get("rating") is not None]
    hist = rating_histogram(rows)
    flags = []
    if expected is not None and len(rows) < expected * 0.8:
        flags.append("incomplete")
    if len(rated) >= 100 and hist.get("5.0", 0) == 0:
        flags.append("no_5_star")
    bad = [row for row in rated if not (0.5 <= row["rating"] <= 5.0)]
    if bad:
        flags.append("invalid_rating")
    return {
        "watched": len(rows),
        "rated": len(rated),
        "unrated": len(rows) - len(rated),
        "expected": expected,
        "histogram": hist,
        "flags": flags,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handles-file", type=Path, default=Path("letterboxd_data/seeds/handles.json"))
    parser.add_argument("--handle", action="append", default=[], help="scrape these handles instead of the file")
    parser.add_argument("--out-dir", type=Path, default=Path("letterboxd_data/seeds"))
    parser.add_argument("--limit", type=int, default=0, help="stop after N profiles (0 = all)")
    parser.add_argument("--min-films", type=int, default=100)
    parser.add_argument("--max-films", type=int, default=2000)
    parser.add_argument("--max-likes", type=int, default=2000)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--force", action="store_true", help="re-scrape even if a done marker exists")
    args = parser.parse_args()

    if args.handle:
        handles = [{"handle": handle, "source": "cli"} for handle in args.handle]
    else:
        payload = load_json(args.handles_file, {})
        raw_handles = payload.get("handles", []) if isinstance(payload, dict) else payload
        handles = []
        for entry in raw_handles:
            if isinstance(entry, str):
                handles.append({"handle": entry, "source": "file"})
            elif isinstance(entry, dict) and entry.get("handle"):
                handles.append(entry)

    if not handles:
        print(f"no handles found in {args.handles_file}")
        return 2

    raw_dir = args.out_dir / "raw"
    likes_dir = args.out_dir / "likes"
    state_dir = args.out_dir / "state"
    for path in (raw_dir, likes_dir, state_dir):
        path.mkdir(parents=True, exist_ok=True)

    mapping_path = args.out_dir / "handles_private.json"
    mapping = load_json(mapping_path, {})
    summary = {"started_at": datetime.now(timezone.utc).isoformat(), "profiles": []}
    attempted = 0

    for entry in handles:
        if args.limit and attempted >= args.limit:
            break
        handle = str(entry["handle"]).strip().lower()
        pseudo = entry.get("pseudonym") or pseudonym(handle)
        mapping[handle] = pseudo
        write_json(mapping_path, mapping)

        done_path = state_dir / f"{pseudo}.done.json"
        if done_path.exists() and not args.force:
            print(f"skip {pseudo}: already done")
            continue

        attempted += 1
        started = time.monotonic()
        print(f"scrape {pseudo} ({entry.get('source', 'file')})")
        try:
            profile_response = fetch(f"{BASE}/{handle}/")
            if is_challenge(profile_response):
                raise ChallengeError(f"anti-bot page on profile {handle}")
            if response_status(profile_response) != 200:
                write_json(state_dir / f"{pseudo}.skip.json", {"reason": "profile unavailable"})
                print(f"  skip: profile unavailable ({response_status(profile_response)})")
                continue
            expected = parse_profile_films(response_html(profile_response))
            if expected is not None and expected < args.min_films:
                write_json(state_dir / f"{pseudo}.skip.json", {"reason": "too few films", "films": expected})
                print(f"  skip: {expected} films < {args.min_films}")
                continue

            film_rows, film_pages = scrape_listing(
                f"{BASE}/{handle}/films/by/rating/", args.max_films, args.max_pages
            )
            like_rows, like_pages = scrape_listing(
                f"{BASE}/{handle}/likes/films/", args.max_likes, args.max_pages
            )
            for row in film_rows:
                row["watched"] = True
                row["source"] = "films"
            for row in like_rows:
                row["liked"] = True
                row["source"] = "likes"

            validation = validate_films(film_rows, expected)
            if expected is not None and len(film_rows) >= args.max_films:
                validation["flags"].append("sampled_cap")

            write_jsonl(raw_dir / f"{pseudo}.jsonl", film_rows)
            write_jsonl(likes_dir / f"{pseudo}.jsonl", like_rows)
            done = {
                "pseudonym": pseudo,
                "source": entry.get("source", "file"),
                "films": validation,
                "likes": len(like_rows),
                "film_pages": film_pages,
                "like_pages": like_pages,
                "seconds": round(time.monotonic() - started, 1),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            write_json(done_path, done)
            summary["profiles"].append(done)
            flag_text = f" flags={','.join(validation['flags'])}" if validation["flags"] else ""
            print(
                f"  watched={validation['watched']} rated={validation['rated']} "
                f"likes={len(like_rows)} pages={film_pages}/{like_pages}{flag_text}"
            )
        except ChallengeError as exc:
            write_json(state_dir / "challenge.json", {"handle": handle, "error": str(exc)})
            print(f"STOP: {exc}")
            return 3
        except Exception as exc:  # keep other profiles moving; record the failure
            write_json(state_dir / f"{pseudo}.error.json", {"error": repr(exc)})
            print(f"  error: {exc}")

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state_dir / "summary.json", summary)
    print(f"done: {len(summary['profiles'])} completed this run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
