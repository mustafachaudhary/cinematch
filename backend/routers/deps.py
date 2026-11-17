"""Shared router dependencies: user resolution and movie formatting."""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from models.database import User, get_db

DEFAULT_USERNAME = "mustafa"


def normalize_username(value: str | None) -> str:
    normalized = (value or "").strip().lstrip("@")
    return normalized.replace(" ", "").lower()


def resolve_user(
    db: Session = Depends(get_db),
    x_username: str | None = Header(None, alias="X-Username"),
) -> User:
    """Map the X-Username header to a User row (auto-provisioning the default
    local user so a fresh install works without signup)."""
    username = normalize_username(x_username) or DEFAULT_USERNAME
    user = db.query(User).filter(User.username == username).first()
    if not user:
        if db.query(User).count() == 0:
            user = User(username=username)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            raise HTTPException(status_code=404, detail=f"Unknown user '{username}'")
    if getattr(user, "is_seed", 0):
        raise HTTPException(
            status_code=403,
            detail="Seed profiles are recommendation data, not login accounts",
        )
    return user


def format_movie(movie, score=None, reason=None, extra=None) -> dict:
    """Canonical JSON shape for a movie in API responses."""
    data = {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "overview": movie.overview,
        "genres": movie.genres.split(",") if movie.genres else [],
        "poster_path": movie.poster_path,
        "release_year": movie.release_year,
        "vote_average": movie.vote_average,
        "popularity": movie.popularity,
        "score": score,
        "reason": reason,
    }
    if extra:
        data.update(extra)
    return data
