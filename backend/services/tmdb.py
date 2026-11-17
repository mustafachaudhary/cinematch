import logging
import os
import random
import threading
import time
from collections import OrderedDict
from functools import lru_cache

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cinematch.tmdb")

BASE_URL = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"
IMAGE_BASE = "https://image.tmdb.org/t/p"
TMDB_API_KEY = os.getenv("TMDB_API_KEY")


class TMDBUnavailable(RuntimeError):
    """TMDB could not be reached (network error or upstream failure).

    Distinct from "the movie does not exist" (404 → None), so callers can
    retry-differently instead of mislabeling an outage as a missing film.
    """


_MAX_ATTEMPTS = 3
_RETRY_STATUS = {429, 500, 502, 503, 504}
_rate_lock = threading.Lock()
_min_interval = 0.0
_last_call = 0.0


def configure_rate_limit(requests_per_second):
    """Throttle all subsequent ``_get`` calls (used by bulk scripts).

    Zero or negative disables pacing (the default, so interactive requests
    are never artificially delayed)."""
    global _min_interval
    _min_interval = 0.0 if requests_per_second <= 0 else 1.0 / requests_per_second


def _pace():
    global _last_call
    if _min_interval <= 0:
        return
    with _rate_lock:
        now = time.monotonic()
        wait = _last_call + _min_interval - now
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _backoff_seconds(attempt):
    return min(8.0, 0.5 * (2 ** attempt)) * (0.5 + random.random())


def _get(endpoint, params=None, timeout=15, attempts=_MAX_ATTEMPTS):
    """Make a GET request to TMDB API. Automatically adds api_key.

    Retries 429/5xx responses with jittered backoff (honoring Retry-After)
    so a single burst cannot abort a bulk enrichment run."""
    if params is None:
        params = {}
    params = dict(params)
    params["api_key"] = TMDB_API_KEY

    url = f"{BASE_URL}{endpoint}"
    last_error = None
    for attempt in range(attempts):
        _pace()
        try:
            response = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
            time.sleep(_backoff_seconds(attempt))
            continue

        if response.status_code in _RETRY_STATUS and attempt < attempts - 1:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = max(0.0, float(retry_after))
            except (TypeError, ValueError):
                delay = _backoff_seconds(attempt)
            time.sleep(delay)
            continue

        response.raise_for_status()
        return response.json()

    raise last_error if last_error else RuntimeError("TMDB request failed")


def image_url(path, size="w500"):
    """Turn a TMDB file path into an absolute image URL."""
    return f"{IMAGE_BASE}/{size}{path}" if path else None


def _release_year(release_date):
    if not release_date:
        return None
    try:
        return int(release_date[:4])
    except (ValueError, IndexError):
        return None


def _certification(release_dates):
    """First US/GB certification in a release-dates payload."""
    for country in ("US", "GB"):
        entry = next(
            (row for row in (release_dates or {}).get("results", [])
             if row.get("iso_3166_1") == country),
            None,
        )
        if not entry:
            continue
        for row in entry.get("release_dates", []):
            cert = (row.get("certification") or "").strip()
            if cert:
                return cert
    return None


_FULL_TTL_SECONDS = 6 * 3600    # successful payloads
_FULL_FAILURE_TTL = 60          # negative cache so outages are not hammered
_FULL_CACHE_MAX = 1024
_full_cache = OrderedDict()
_full_cache_lock = threading.Lock()


def get_movie_full(tmdb_id):
    """Complete, normalized film record for the overview page.

    One TMDB request with the append payloads the UI uses, cached for six
    hours (failures briefly, so a flaky upstream is retried without hammering
    it). Returns None when TMDB cannot provide the record; callers fall back
    to the cached catalog row.
    """
    now = time.time()
    with _full_cache_lock:
        hit = _full_cache.get(tmdb_id)
        if hit is not None:
            ts, payload = hit
            ttl = _FULL_TTL_SECONDS if payload is not None else _FULL_FAILURE_TTL
            if now - ts < ttl:
                _full_cache.move_to_end(tmdb_id)
                return payload

    try:
        payload = _build_movie_full(tmdb_id)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status != 404:
            logger.warning("TMDB request failed for movie %s", tmdb_id, exc_info=True)
        payload = None

    with _full_cache_lock:
        _full_cache[tmdb_id] = (time.time(), payload)
        _full_cache.move_to_end(tmdb_id)
        while len(_full_cache) > _FULL_CACHE_MAX:
            _full_cache.popitem(last=False)
    return payload


_CREDITS_TTL_SECONDS = 6 * 3600
_CREDITS_CACHE_MAX = 2048
_credits_cache = OrderedDict()
_credits_cache_lock = threading.Lock()


def _normalize_credits(data):
    """Normalize TMDB credits into the cast/crew shape used by the app."""
    cast = [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "character": person.get("character"),
            "profile_path": image_url(person.get("profile_path"), "w185"),
            "order": person.get("order"),
        }
        for person in (data.get("cast") or [])[:24]
        if person.get("id") and person.get("name")
    ]

    crew = data.get("crew") or []
    directors = [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "profile_path": image_url(person.get("profile_path"), "w185"),
        }
        for person in crew
        if person.get("job") == "Director" and person.get("id") and person.get("name")
    ]
    writers = [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "profile_path": image_url(person.get("profile_path"), "w185"),
        }
        for person in crew
        if person.get("job") in {"Writer", "Screenplay", "Story"}
        and person.get("id")
        and person.get("name")
    ]
    return {"cast": cast, "directors": directors, "writers": writers}


def get_movie_credits(tmdb_id):
    """Lightweight credits-only payload, cached separately from the full film.

    Movie detail pages use this to lazily fill missing cast portraits without
    pulling the larger images/videos payload when the film is already local.
    """
    now = time.time()
    with _credits_cache_lock:
        hit = _credits_cache.get(tmdb_id)
        if hit is not None:
            ts, payload = hit
            ttl = _CREDITS_TTL_SECONDS if payload is not None else _FULL_FAILURE_TTL
            if now - ts < ttl:
                _credits_cache.move_to_end(tmdb_id)
                return payload

    try:
        payload = _normalize_credits(
            _get(
                f"/movie/{tmdb_id}/credits",
                {"language": "en-US"},
                timeout=6,
                attempts=1,
            )
        )
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status != 404:
            logger.warning("TMDB credits request failed for movie %s", tmdb_id, exc_info=True)
        payload = None

    with _credits_cache_lock:
        _credits_cache[tmdb_id] = (time.time(), payload)
        _credits_cache.move_to_end(tmdb_id)
        while len(_credits_cache) > _CREDITS_CACHE_MAX:
            _credits_cache.popitem(last=False)
    return payload


def _build_movie_full(tmdb_id):
    data = _get(
        f"/movie/{tmdb_id}",
        {
            "language": "en-US",
            "append_to_response": (
                "credits,keywords,images,videos,release_dates,similar,recommendations"
            ),
            "include_image_language": "en,null",
        },
    )
    if not data.get("title"):
        return None

    credits = data.get("credits") or {}
    cast = [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "character": person.get("character"),
            "profile_path": image_url(person.get("profile_path"), "w185"),
            "order": person.get("order"),
        }
        for person in (credits.get("cast") or [])[:24]
    ]

    key_jobs = {
        "Director",
        "Writer",
        "Screenplay",
        "Story",
        "Director of Photography",
        "Original Music Composer",
        "Editor",
        "Producer",
        "Executive Producer",
        "Production Design",
        "Costume Design",
    }
    crew = []
    seen_crew = set()
    for person in credits.get("crew") or []:
        job = person.get("job")
        if job not in key_jobs:
            continue
        key = (person.get("id"), job)
        if key in seen_crew:
            continue
        seen_crew.add(key)
        crew.append(
            {
                "id": person.get("id"),
                "name": person.get("name"),
                "job": job,
                "department": person.get("department"),
                "profile_path": image_url(person.get("profile_path"), "w185"),
            }
        )
    crew = crew[:24]

    directors = [
        {"id": p["id"], "name": p["name"], "profile_path": p["profile_path"]}
        for p in crew
        if p["job"] == "Director"
    ]
    writers = [
        p for p in crew
        if p["job"] in {"Writer", "Screenplay", "Story"}
    ]

    videos = [
        {
            "id": video.get("id"),
            "key": video.get("key"),
            "name": video.get("name"),
            "site": video.get("site"),
            "type": video.get("type"),
            "official": bool(video.get("official")),
        }
        for video in (data.get("videos") or {}).get("results", [])
        if video.get("site") == "YouTube" and video.get("key")
    ][:8]
    # Trailers first, official before unofficial; stable within each group.
    videos.sort(key=lambda v: (v["type"] != "Trailer", not v["official"]))

    images = data.get("images") or {}
    backdrops = [
        image_url(img.get("file_path"), "w1280")
        for img in images.get("backdrops", [])
        if img.get("file_path")
    ][:8]
    posters = [
        image_url(img.get("file_path"), "w500")
        for img in images.get("posters", [])
        if img.get("file_path")
    ][:8]

    similar_source = (data.get("similar") or {}).get("results") or []
    if not similar_source:
        similar_source = (data.get("recommendations") or {}).get("results") or []
    similar = [
        {
            "tmdb_id": movie.get("id"),
            "title": movie.get("title"),
            "poster_path": image_url(movie.get("poster_path"), "w500"),
            "release_year": _release_year(movie.get("release_date")),
            "vote_average": movie.get("vote_average"),
        }
        for movie in similar_source[:10]
        if movie.get("id") and movie.get("title")
    ]
    related = []
    seen_related = set()
    for source in ("similar", "recommendations"):
        for rank, movie in enumerate((data.get(source) or {}).get("results") or []):
            related_id = movie.get("id")
            if not related_id or related_id == data.get("id"):
                continue
            key = (int(related_id), source)
            if key in seen_related:
                continue
            seen_related.add(key)
            related.append(
                {
                    "related_tmdb_id": int(related_id),
                    "source": source,
                    "rank": rank,
                }
            )

    return {
        "tmdb_id": data.get("id"),
        "title": data.get("title"),
        "original_title": data.get("original_title"),
        "collection_id": (data.get("belongs_to_collection") or {}).get("id"),
        "tagline": (data.get("tagline") or "").strip() or None,
        "overview": (data.get("overview") or "").strip(),
        "status": data.get("status"),
        "release_date": data.get("release_date") or None,
        "release_year": _release_year(data.get("release_date")),
        "runtime": data.get("runtime") or None,
        "genres": [g.get("name") for g in data.get("genres", []) if g.get("name")],
        "poster_path": image_url(data.get("poster_path"), "w500"),
        "backdrop_path": image_url(data.get("backdrop_path"), "w1280"),
        "vote_average": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "popularity": data.get("popularity"),
        "original_language": data.get("original_language"),
        "spoken_languages": [
            lang.get("english_name") or lang.get("name")
            for lang in data.get("spoken_languages", [])
            if lang.get("english_name") or lang.get("name")
        ],
        "production_countries": [
            country.get("name")
            for country in data.get("production_countries", [])
            if country.get("name")
        ],
        "production_companies": [
            {
                "name": company.get("name"),
                "logo_path": image_url(company.get("logo_path"), "w92"),
                "origin_country": company.get("origin_country"),
            }
            for company in data.get("production_companies", [])
            if company.get("name")
        ],
        "budget": data.get("budget", 0),
        "revenue": data.get("revenue", 0),
        "homepage": data.get("homepage") or None,
        "imdb_id": data.get("imdb_id") or None,
        "certification": _certification(data.get("release_dates")),
        "cast": cast,
        "crew": crew,
        "directors": directors,
        "writers": writers,
        "keywords": [
            keyword.get("name")
            for keyword in (data.get("keywords") or {}).get("keywords", [])
            if keyword.get("name")
        ],
        "videos": videos,
        "images": {"backdrops": backdrops, "posters": posters},
        "similar": similar,
        "related": related,
    }


def get_movie_metadata(tmdb_id):
    """Lean metadata payload for catalog enrichment.

    One request per film with credits, keywords, external ids, and related
    films. Kept separate from ``get_movie_full`` so bulk enrichment does not
    pull images/videos/release-date payloads it will never store.
    """
    data = _get(
        f"/movie/{tmdb_id}",
        {
            "language": "en-US",
            "append_to_response": "credits,keywords,external_ids,similar,recommendations",
        },
    )
    if not data.get("title"):
        return None

    credits = data.get("credits") or {}
    people = []
    seen_people = set()

    def add_person(person, role, ordering=None):
        person_id = person.get("id")
        name = (person.get("name") or "").strip()
        if not person_id or not name:
            return
        key = (int(person_id), role)
        if key in seen_people:
            return
        seen_people.add(key)
        people.append(
            {
                "person_id": int(person_id),
                "name": name,
                "role": role,
                "ordering": ordering,
                "profile_path": image_url(person.get("profile_path"), "w185"),
            }
        )

    for person in credits.get("crew") or []:
        job = person.get("job")
        if job == "Director":
            add_person(person, "director")
        elif job in {"Writer", "Screenplay", "Story"}:
            add_person(person, "writer")
    for person in (credits.get("cast") or [])[:10]:
        add_person(person, "cast", person.get("order"))

    related = []
    seen_related = set()
    for source in ("similar", "recommendations"):
        results = (data.get(source) or {}).get("results") or []
        for rank, movie in enumerate(results[:12]):
            related_id = movie.get("id")
            if not related_id or related_id == tmdb_id:
                continue
            key = (int(related_id), source)
            if key in seen_related:
                continue
            seen_related.add(key)
            related.append(
                {
                    "related_tmdb_id": int(related_id),
                    "source": source,
                    "rank": rank,
                }
            )

    collection = data.get("belongs_to_collection") or {}
    external = data.get("external_ids") or {}
    release_year = _release_year(data.get("release_date"))
    poster_path = (
        f"{POSTER_BASE}{data['poster_path']}" if data.get("poster_path") else None
    )
    return {
        "tmdb_id": int(data["id"]),
        "title": data.get("title") or "",
        "overview": (data.get("overview") or "").strip(),
        "genres": [g.get("name") for g in data.get("genres", []) if g.get("name")],
        "poster_path": poster_path,
        "release_year": release_year,
        "popularity": data.get("popularity") or 0.0,
        "vote_average": data.get("vote_average") or 0.0,
        "vote_count": data.get("vote_count"),
        "runtime": data.get("runtime") or None,
        "original_language": data.get("original_language"),
        "collection_id": collection.get("id"),
        "imdb_id": external.get("imdb_id") or data.get("imdb_id"),
        "keywords": [
            keyword.get("name")
            for keyword in (data.get("keywords") or {}).get("keywords", [])
            if keyword.get("name")
        ],
        "people": people,
        "related": related,
    }


@lru_cache(maxsize=1)
def get_genre_map():
    """Fetch genre mapping from TMDB. Returns {genre_id: genre_name}.

    The mapping is effectively static; cached for the process instead of
    spending a round trip per search/import call."""
    data = _get("/genre/movie/list")
    return {g["id"]: g["name"] for g in data.get("genres", [])}


def _normalize(movie_dict, genre_map):
    """Normalize a raw TMDB movie dict. Returns None if missing title."""
    title = movie_dict.get("title", "").strip()
    # allow empty overview (don't drop movies just because overview is missing)
    overview = movie_dict.get("overview") or ""
    overview = overview.strip() if isinstance(overview, str) else ""
    
    if not title:
        return None
    
    # Build poster URL
    poster_path = None
    if movie_dict.get("poster_path"):
        poster_path = f"{POSTER_BASE}{movie_dict['poster_path']}"
    
    # Extract release year
    release_year = None
    release_date = movie_dict.get("release_date", "")
    if release_date:
        try:
            release_year = int(release_date[:4])
        except (ValueError, IndexError):
            pass
    
    # Map genres from IDs
    genre_ids = movie_dict.get("genre_ids", [])
    genres = [genre_map.get(gid) for gid in genre_ids if gid in genre_map]
    
    return {
        "tmdb_id": movie_dict["id"],
        "title": title,
        "overview": overview,
        "genres": genres,
        "poster_path": poster_path,
        "release_year": release_year,
        "popularity": movie_dict.get("popularity", 0.0),
        "vote_average": movie_dict.get("vote_average", 0.0),
    }


def fetch_popular_movies(pages=10):
    """Fetch popular movies. Returns list of normalized movies."""
    genre_map = get_genre_map()
    movies = []
    
    for page in range(1, pages + 1):
        data = _get("/movie/popular", {"page": page, "language": "en-US"})
        for movie in data.get("results", []):
            normalized = _normalize(movie, genre_map)
            if normalized:
                movies.append(normalized)
    
    return movies


def fetch_top_rated_movies(pages=10):
    """Fetch top-rated movies. Returns list of normalized movies."""
    genre_map = get_genre_map()
    movies = []

    for page in range(1, pages + 1):
        data = _get("/movie/top_rated", {"page": page, "language": "en-US"})
        for movie in data.get("results", []):
            normalized = _normalize(movie, genre_map)
            if normalized:
                movies.append(normalized)

    return movies


def fetch_genre_movies(genre_id, pages=2):
    """Fetch popular movies within one genre via /discover."""
    genre_map = get_genre_map()
    movies = []

    for page in range(1, pages + 1):
        data = _get(
            "/discover/movie",
            {
                "with_genres": genre_id,
                "sort_by": "popularity.desc",
                "page": page,
                "language": "en-US",
                "vote_count.gte": 200,
            },
        )
        for movie in data.get("results", []):
            normalized = _normalize(movie, genre_map)
            if normalized:
                movies.append(normalized)

    return movies


def search_movies(query, year=None, limit=10):
    """Search movies by title. Accepts optional `year` to assist matching.
    Returns list of normalized movies."""
    genre_map = get_genre_map()
    params = {"query": query, "language": "en-US", "page": 1}
    if year:
        # TMDB accepts `year` for narrowing results
        params["year"] = year

    data = _get("/search/movie", params)
    
    movies = []
    for movie in data.get("results", [])[:limit]:
        normalized = _normalize(movie, genre_map)
        if normalized:
            movies.append(normalized)
    
    return movies


def find_by_imdb(imdb_id):
    """Resolve an IMDb id to a normalized TMDB movie (exact external lookup).

    Used by the seed resolver only when title+year search is ambiguous. The
    ``/find`` endpoint is an exact external-id lookup, so the first movie
    result is the canonical match.
    """
    imdb_id = (imdb_id or "").strip()
    if not imdb_id.startswith("tt") or not imdb_id[2:].isdigit():
        return None
    genre_map = get_genre_map()
    data = _get(
        f"/find/{imdb_id}",
        {"external_source": "imdb_id", "language": "en-US"},
    )
    movies = []
    for raw in data.get("movie_results") or []:
        normalized = _normalize(raw, genre_map)
        if normalized:
            movies.append(normalized)
    if not movies:
        return None
    movies.sort(
        key=lambda m: (m.get("popularity") or 0.0, m.get("vote_average") or 0.0),
        reverse=True,
    )
    return movies[0]


def get_movie_details(tmdb_id):
    """Get detailed info for a movie.

    Returns the normalized dict, or None when TMDB says the movie does not
    exist (404). Raises TMDBUnavailable when TMDB itself cannot be reached —
    the two cases must not be collapsed into "not found".
    """
    try:
        genre_map = get_genre_map()
        movie_dict = _get(f"/movie/{tmdb_id}")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            return None
        raise TMDBUnavailable(f"TMDB responded {status}") from exc
    except requests.RequestException as exc:
        raise TMDBUnavailable(str(exc)) from exc

    # Convert genres list to genre_ids for _normalize
    if "genres" in movie_dict:
        movie_dict["genre_ids"] = [g["id"] for g in movie_dict["genres"]]

    return _normalize(movie_dict, genre_map)


def get_similar_movies(tmdb_id, limit=20):
    """Get similar movies for a given movie. Returns list of normalized movies."""
    genre_map = get_genre_map()
    data = _get(f"/movie/{tmdb_id}/similar")
    
    movies = []
    for movie in data.get("results", [])[:limit]:
        normalized = _normalize(movie, genre_map)
        if normalized:
            movies.append(normalized)
    
    return movies
