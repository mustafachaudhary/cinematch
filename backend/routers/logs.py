from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import LoggedMovie, MovieCache, get_db
from routers.deps import format_movie, resolve_user
from services.ratings import remove_user_rating, set_user_rating
from services.tmdb import TMDBUnavailable

router = APIRouter(prefix="/logs", tags=["logs"])


class LogMovieRequest(BaseModel):
    tmdb_id: int
    rating: float = Field(..., ge=0.5, le=5.0)


def format_logged_movie(
    db: Session,
    user_id: int,
    tmdb_id: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """A user's ratings joined with catalog content, newest first.

    ``tmdb_id`` narrows to a single film; ``limit``/``offset`` add optional
    pagination while the defaults keep the original full-list behavior.
    """
    query = (
        db.query(LoggedMovie, MovieCache)
        .join(MovieCache, LoggedMovie.tmdb_id == MovieCache.tmdb_id)
        .filter(LoggedMovie.user_id == user_id)
        .order_by(LoggedMovie.logged_at.desc())
    )
    if tmdb_id is not None:
        query = query.filter(LoggedMovie.tmdb_id == tmdb_id)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    return [
        {
            "id": logged.id,
            "tmdb_id": logged.tmdb_id,
            "title": movie.title,
            "poster_path": movie.poster_path,
            "genres": movie.genres.split(",") if movie.genres else [],
            "release_year": movie.release_year,
            "rating": logged.rating,
            "logged_at": logged.logged_at.isoformat() if logged.logged_at else None,
        }
        for logged, movie in rows
    ]


@router.get("/")
def get_logs(
    limit: int | None = Query(None, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    return format_logged_movie(db, user.user_id, limit=limit, offset=offset)


@router.post("/")
def log_movie(req: LogMovieRequest, db: Session = Depends(get_db), user=Depends(resolve_user)):
    try:
        set_user_rating(db, user.user_id, req.tmdb_id, req.rating)
    except TMDBUnavailable:
        raise HTTPException(
            status_code=503,
            detail="TMDB is unreachable right now — the film can't be cataloged yet. Try again shortly.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cached = db.query(MovieCache).filter(MovieCache.tmdb_id == req.tmdb_id).first()
    # Only the row that changed — rebuilding the whole history per write made
    # POST /logs O(total ratings).
    entry = next(
        iter(format_logged_movie(db, user.user_id, tmdb_id=req.tmdb_id)), None
    )
    if entry and cached:
        entry["movie"] = format_movie(cached)
    return entry or {"tmdb_id": req.tmdb_id, "rating": req.rating}


@router.delete("/{tmdb_id}")
def remove_log(tmdb_id: int, db: Session = Depends(get_db), user=Depends(resolve_user)):
    if not remove_user_rating(db, user.user_id, tmdb_id):
        raise HTTPException(status_code=404, detail="Movie not in your logs")
    return {"deleted": tmdb_id}
