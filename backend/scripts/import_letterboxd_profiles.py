"""Import real rater profiles from public Letterboxd pages.

Scrapes each user's top-rated films (title, year, star rating), resolves them
against TMDB, caches + embeds the films, and writes them as ratings for a
`lb_<username>` profile — giving item-item collaborative filtering real
co-rating data.

Run:  python scripts/import_letterboxd_profiles.py <user1> [user2 ...] [--per-user N]
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapling.fetchers import Fetcher

from models.database import LoggedMovie, SessionLocal, User, UserProfile
from services.ratings import ensure_cached_movie, set_user_rating
from services import tmdb

RATED_CLASS = re.compile(r"rated-(\d)")
NAME_YEAR = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")


def scrape_user_ratings(username: str, per_user: int, max_pages: int = 8):
    """[(title, year|None, rating)] from /<user>/films/by/rating/ pages."""
    out = []
    for page_num in range(1, max_pages + 1):
        url = f"https://letterboxd.com/{username}/films/by/rating/"
        if page_num > 1:
            url += f"page/{page_num}/"
        page = Fetcher.get(url)
        if page.status != 200:
            if page_num == 1:
                print(f"  ! {username}: HTTP {page.status} — skipping")
            break
        items = page.css("li.griditem") + page.css("li.poster-container")
        added = 0
        for li in items:
            node = (li.css("div.react-component") or [None])[0]
            span = (li.css("span.rating") or [None])[0]
            if node is None or span is None:
                continue
            rated = RATED_CLASS.search(span.attrib.get("class", ""))
            if not rated:
                continue
            raw_name = node.attrib.get("data-item-name", "").strip()
            m = NAME_YEAR.match(raw_name)
            title, year = (m.group(1), int(m.group(2))) if m else (raw_name, None)
            if not title:
                continue
            rating = min(5.0, max(0.5, int(rated.group(1)) / 2))
            out.append((title, year, rating))
            added += 1
            if len(out) >= per_user:
                return out
        if added == 0:
            break
        time.sleep(1.0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("users", nargs="+")
    ap.add_argument("--per-user", type=int, default=120)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        for username in args.users:
            username = username.strip().lstrip("@").lower()
            handle = f"lb_{username}"
            if db.query(User).filter(User.username == handle).first():
                print(f"= {handle} already imported, skipping")
                continue

            entries = scrape_user_ratings(username, args.per_user)
            if not entries:
                continue
            print(f"  {username}: scraped {len(entries)} rated films")

            user = User(username=handle)
            db.add(user)
            db.flush()
            db.add(UserProfile(username=handle, display_name=f"{username} (Letterboxd)"))

            imported = skipped = 0
            for title, year, rating in entries:
                try:
                    # Savepoint so one bad row can't roll back the pending user.
                    with db.begin_nested():
                        results = tmdb.search_movies(title, year=year, limit=5)
                        if not results:
                            raise LookupError("no TMDB match")
                        movie_data = results[0]
                        if year:
                            for candidate in results:
                                if candidate.get("release_year") == year:
                                    movie_data = candidate
                                    break
                        ensure_cached_movie(db, movie_data)
                        set_user_rating(
                            db, user.user_id, movie_data["tmdb_id"], rating, commit=False
                        )
                    imported += 1
                except Exception as exc:  # noqa: BLE001 — one bad row shouldn't kill an import
                    skipped += 1
                    print(f"    skip '{title}' ({year}): {exc}")
            db.commit()
            print(f"  -> lb_{username}: {imported} imported, {skipped} unresolved")

        raters = db.query(LoggedMovie.user_id).distinct().count()
        total = db.query(LoggedMovie).count()
        print(f"Done. {raters} rater(s) now hold {total} ratings.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
