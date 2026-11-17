"""Discover candidate Letterboxd profiles from popular-member and review pages.

Usage:
    cd backend && ../.venv/bin/python scripts/letterboxd_handles.py \
        --target 120 --out letterboxd_data/seeds/handles.json \
        --private letterboxd_data/seeds/handles_private.json

Only public pages are read, one request every 1.2–2.0 s. The scraper
pre-screens each candidate by the profile's Films count before harvesting.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from letterboxd_common import (
    BASE,
    ChallengeError,
    fetch,
    is_challenge,
    load_json,
    pseudonym,
    response_html,
    response_status,
    write_json,
)

PROFILE_LINK_RE = re.compile(r"^/([A-Za-z0-9_-]+)/$")
REVIEW_LINK_RE = re.compile(r"^/([A-Za-z0-9_-]+)/film/")

NON_USER_PATHS = {
    "about", "api-beta", "apps", "contact", "crew", "film", "films", "journal",
    "lists", "members", "reviews", "likes", "settings", "welcome", "pro", "gift",
    "legal", "privacy", "terms", "jobs", "podcast", "newsletter", "store", "shop",
    "help", "faq", "sign-in", "sign-up", "logout", "search", "activity", "rss",
    "year-in-review",
}

DEFAULT_REVIEW_FILMS = [
    "interstellar",
    "the-dark-knight",
    "parasite-2019",
    "everything-everywhere-all-at-once",
    "dune-part-two",
    "oppenheimer-2023",
    "the-godfather",
    "pulp-fiction",
    "spirited-away",
    "la-la-land",
    "the-shining",
    "inception",
    "fight-club",
    "whiplash-2014",
    "arrival-2016",
]


def existing_handles(db_path: Path) -> set[str]:
    """Old seed usernames encode the raw handle: lb_<handle>."""
    if not db_path.exists():
        return set()
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        names = [row[0] for row in con.execute("select username from users where username like 'lb_%'")]
        con.close()
        return {name[3:] for name in names if name.startswith("lb_")}
    except sqlite3.Error:
        return set()


def handles_from_members(limit: int, max_pages: int) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for period in ("month", "year", "week"):
        url = f"{BASE}/members/popular/this/{period}/"
        for page in range(1, max_pages + 1):
            response = fetch(url)
            if is_challenge(response):
                raise ChallengeError(f"challenge on {url}")
            if response_status(response) != 200:
                break
            soup = BeautifulSoup(response_html(response), "lxml")
            for anchor in soup.select("a[href]"):
                match = PROFILE_LINK_RE.match(anchor.get("href", ""))
                if not match:
                    continue
                handle = match.group(1).lower()
                if handle in NON_USER_PATHS:
                    continue
                found.append((handle, f"members/{period}"))
            next_link = None
            for anchor in soup.select("a.next, a[rel=next]"):
                next_link = anchor.get("href")
                if next_link:
                    break
            if not next_link or len(found) >= limit:
                break
            url = next_link if next_link.startswith("http") else f"{BASE}{next_link}"
    return found


def handles_from_reviews(films: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for slug in films:
        url = f"{BASE}/film/{slug}/reviews/"
        response = fetch(url)
        if is_challenge(response):
            raise ChallengeError(f"challenge on {url}")
        if response_status(response) != 200:
            continue
        soup = BeautifulSoup(response_html(response), "lxml")
        for anchor in soup.select("a[href]"):
            match = REVIEW_LINK_RE.match(anchor.get("href", ""))
            if not match:
                continue
            handle = match.group(1).lower()
            if handle not in NON_USER_PATHS:
                found.append((handle, f"reviews/{slug}"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=120)
    parser.add_argument("--member-pages", type=int, default=3)
    parser.add_argument("--review-films", default=",".join(DEFAULT_REVIEW_FILMS))
    parser.add_argument("--out", type=Path, default=Path("letterboxd_data/seeds/handles.json"))
    parser.add_argument("--private", type=Path, default=Path("letterboxd_data/seeds/handles_private.json"))
    parser.add_argument("--db", type=Path, default=Path("cinematch.db"))
    args = parser.parse_args()

    exclude = existing_handles(args.db)
    print(f"excluding {len(exclude)} existing seed handles")

    candidates: list[tuple[str, str]] = []
    try:
        candidates += handles_from_members(args.target, args.member_pages)
        print(f"members: {len(candidates)} raw candidates")
        candidates += handles_from_reviews(
            [film.strip() for film in args.review_films.split(",") if film.strip()]
        )
        print(f"with reviews: {len(candidates)} raw candidates")
    except ChallengeError as exc:
        print(f"STOP: {exc}")
        return 3

    selected: list[dict] = []
    seen: set[str] = set()
    for handle, source in candidates:
        if handle in exclude or handle in seen:
            continue
        seen.add(handle)
        selected.append({"handle": handle, "source": source, "pseudonym": pseudonym(handle)})
        if len(selected) >= args.target:
            break

    mapping = load_json(args.private, {})
    for entry in selected:
        mapping[entry["handle"]] = entry["pseudonym"]
    write_json(args.private, mapping)
    write_json(
        args.out,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(selected),
            "handles": selected,
        },
    )
    print(f"wrote {len(selected)} candidates to {args.out}")
    print(f"private handle mapping: {args.private}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
