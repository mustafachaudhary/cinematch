import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_API_KEY = os.getenv("TMDB_API_KEY")


def _get(endpoint, params=None):
    """Make a GET request to TMDB API. Automatically adds api_key."""
    if params is None:
        params = {}
    params["api_key"] = TMDB_API_KEY
    
    url = f"{BASE_URL}{endpoint}"
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()


def get_genre_map():
    """Fetch genre mapping from TMDB. Returns {genre_id: genre_name}."""
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


def get_movie_details(tmdb_id):
    """Get detailed info for a movie. Returns normalized dict or None."""
    try:
        genre_map = get_genre_map()
        movie_dict = _get(f"/movie/{tmdb_id}")
        
        # Convert genres list to genre_ids for _normalize
        if "genres" in movie_dict:
            movie_dict["genre_ids"] = [g["id"] for g in movie_dict["genres"]]
        
        return _normalize(movie_dict, genre_map)
    except Exception:
        return None


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
