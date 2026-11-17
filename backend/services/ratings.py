"""Shared rating pipeline.

Every explicit rating — whether from the UI or a Letterboxd import — flows
through here so that the display table (logged_movies), the CF matrix
(films + ratings) and the embedding cache stay consistent.
"""

import json

from models.database import Film, LoggedMovie, MovieCache, Rating
from services import embeddings, tmdb


def ensure_cached_movie(db, movie_data) -> MovieCache:
    """Make sure a normalized TMDB movie dict exists in the catalog."""
    cached = db.query(MovieCache).filter(MovieCache.tmdb_id == movie_data["tmdb_id"]).first()
    if cached:
        return cached
    cached = MovieCache(
        tmdb_id=movie_data["tmdb_id"],
        title=movie_data["title"],
        overview=movie_data.get("overview") or "",
        genres=",".join(movie_data.get("genres") or []),
        poster_path=movie_data.get("poster_path"),
        release_year=movie_data.get("release_year"),
        popularity=movie_data.get("popularity", 0.0),
        vote_average=movie_data.get("vote_average", 0.0),
    )
    db.add(cached)
    db.flush()
    # Embed inline so the new film is a first-class candidate immediately.
    provider = embeddings.get_provider(db)
    text = embeddings.movie_text(
        cached.title, cached.overview, (cached.genres or "").split(",")
    )
    cached.embedding = json.dumps(
        [round(float(x), 5) for x in provider.encode_one(text)]
    )
    return cached


def set_user_rating(db, user_id: int, tmdb_id: int, rating: float, commit=True, logged_at=None):
    """Create/update a user's rating across logged_movies and the CF matrix.

    ``logged_at`` (optional) carries the real rating date from imports that
    have one (Letterboxd ZIP ``Date`` column); without it the row keeps the
    default "now" stamp. Returns the LoggedMovie row. Raises ValueError when
    the movie is unknown to TMDB and therefore cannot be cataloged.
    """
    if not (0.5 <= rating <= 5.0):
        raise ValueError("Rating must be between 0.5 and 5.0 stars")

    cached = db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).first()
    if not cached:
        details = tmdb.get_movie_details(tmdb_id)
        if not details:
            raise ValueError(f"Movie {tmdb_id} not found")
        cached = ensure_cached_movie(db, details)

    # Display table
    logged = (
        db.query(LoggedMovie)
        .filter(LoggedMovie.user_id == user_id, LoggedMovie.tmdb_id == tmdb_id)
        .first()
    )
    if logged:
        logged.rating = rating
        if logged_at is not None:
            logged.logged_at = logged_at
    else:
        logged = LoggedMovie(user_id=user_id, tmdb_id=tmdb_id, rating=rating)
        if logged_at is not None:
            logged.logged_at = logged_at
        db.add(logged)

    # CF matrix
    db.merge(Film(film_id=cached.tmdb_id, title=cached.title, year=cached.release_year))
    db.merge(
        Rating(
            user_id=user_id,
            film_id=tmdb_id,
            rating=rating,
            rated_at=logged_at,
        )
    )

    if commit:
        db.commit()
        db.refresh(logged)
    return logged


def remove_user_rating(db, user_id: int, tmdb_id: int):
    logged = (
        db.query(LoggedMovie)
        .filter(LoggedMovie.user_id == user_id, LoggedMovie.tmdb_id == tmdb_id)
        .first()
    )
    if not logged:
        return False
    db.delete(logged)
    db.query(Rating).filter(
        Rating.user_id == user_id, Rating.film_id == tmdb_id
    ).delete()
    db.commit()
    return True
