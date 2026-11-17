"""Shared, rate-limited Letterboxd fetch/parse helpers for seed harvesting.

The public profile pages are fetched with Scrapling's ``Fetcher`` (no browser),
one request at a time with a 1.2–2.0 s jittered gap. The parser captures the
whole watched grid (rated *and* unrated) plus the separate likes grid.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from scrapling.fetchers import Fetcher

RATING_RE = re.compile(r"^rated-(\d{1,2})$")
TITLE_YEAR_RE = re.compile(r"^(.*?)\s+\((\d{4})\)$")
PROFILE_FILMS_RE = re.compile(r"([\d,]+)\s+Films", re.IGNORECASE)
IMDB_RE = re.compile(r"(?:imdb\.com/title/|data-imdb-id=[\"'])(tt\d{7,10})")

_last_request = 0.0
_throttle_lock = threading.Lock()
BASE = "https://letterboxd.com"


class ChallengeError(RuntimeError):
    """Letterboxd served an anti-bot page; stop rather than escalate."""


def throttle(min_seconds: float = 1.2, max_seconds: float = 2.0) -> None:
    global _last_request
    # A single global spacing protects Letterboxd even when a caller uses a
    # small worker pool.  The lock is held only while reserving the next slot,
    # not during the HTTP request itself.
    with _throttle_lock:
        wait = _last_request + random.uniform(min_seconds, max_seconds) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def fetch(
    url: str,
    timeout: int = 30,
    cookies: dict | None = None,
    min_seconds: float = 1.2,
    max_seconds: float = 2.0,
):
    throttle(min_seconds=min_seconds, max_seconds=max_seconds)
    kwargs = {"timeout": timeout}
    if cookies:
        kwargs["cookies"] = cookies
    return Fetcher.get(url, **kwargs)


def response_status(response) -> int:
    try:
        return int(getattr(response, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def response_html(response) -> str:
    return (
        getattr(response, "html_content", None)
        or getattr(response, "text", None)
        or ""
    )


def is_challenge(response) -> bool:
    """Detect an actual anti-bot interstitial, not the login form's captcha attr."""
    body = response_html(response).lower()
    challenge_markers = (
        "just a moment...",
        "attention required",
        "enable javascript and cookies to continue",
        "cf-browser-verification",
        "cf-challenge",
    )
    return response_status(response) in (403, 429) or any(
        marker in body for marker in challenge_markers
    )


def parse_grid(page_html: str) -> list[dict]:
    """Parse one Letterboxd poster grid into rated/unrated watched rows."""
    soup = BeautifulSoup(page_html, "lxml")
    rows: dict[str, dict] = {}
    for item in soup.select("[data-item-slug]"):
        slug = (item.get("data-item-slug") or "").strip()
        if not slug:
            continue
        display = (
            item.get("data-item-full-display-name")
            or item.get("data-item-name")
            or ""
        ).strip()
        match = TITLE_YEAR_RE.match(display)
        title = match.group(1).strip() if match else display
        year = int(match.group(2)) if match else None

        container = item.find_parent("li") or item
        rating_span = container.select_one("span.rating")
        rating = None
        if rating_span:
            for css_class in rating_span.get("class", []):
                rating_match = RATING_RE.match(css_class)
                if not rating_match:
                    continue
                tenths = int(rating_match.group(1))
                if 1 <= tenths <= 10:
                    rating = tenths / 2.0
                break

        row = {
            "slug": slug,
            "title": title,
            "year": year,
            "rating": rating,
        }
        previous = rows.get(slug)
        if previous is None or (previous.get("rating") is None and rating is not None):
            rows[slug] = row
    return list(rows.values())


def parse_profile_films(page_html: str) -> int | None:
    soup = BeautifulSoup(page_html, "lxml")
    for element in soup.select(".profile-statistic"):
        match = PROFILE_FILMS_RE.search(element.get_text(" ", strip=True))
        if match:
            return int(match.group(1).replace(",", ""))
    match = PROFILE_FILMS_RE.search(soup.get_text(" ", strip=True))
    return int(match.group(1).replace(",", "")) if match else None


def extract_imdb_id(page_html: str) -> str | None:
    """Return the IMDb id linked from a Letterboxd film page, if present."""
    match = IMDB_RE.search(page_html or "")
    return match.group(1) if match else None


def next_page_url(page_html: str, current_url: str) -> str | None:
    """Return the forward pagination link, never the 'Newer' (back) link."""
    soup = BeautifulSoup(page_html, "lxml")
    for anchor in soup.select("a[rel=next], a.next"):
        href = anchor.get("href")
        if href:
            return urljoin(current_url, href)
    for wanted in ("older", "lower", "next"):
        for anchor in soup.select("a[href]"):
            if anchor.get_text(" ", strip=True).lower() == wanted:
                return urljoin(current_url, anchor.get("href"))
    return None


def pseudonym(handle: str) -> str:
    digest = hashlib.sha1(handle.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"lb_{digest}"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.replace(temp, path)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default
