from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models.database import MovieCache, WatchlistItem, get_db
from routers.deps import format_movie, resolve_user

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _format_watchlist_item(item: WatchlistItem, movie: MovieCache) -> dict:
    payload = format_movie(movie)
    payload["added_at"] = item.added_at.isoformat() if item.added_at else None
    return payload


def _movie_or_404(db: Session, tmdb_id: int) -> MovieCache:
    movie = db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail="Unknown film")
    return movie


@router.get("/")
def get_watchlist(db: Session = Depends(get_db), user=Depends(resolve_user)):
    rows = (
        db.query(WatchlistItem, MovieCache)
        .join(MovieCache, MovieCache.tmdb_id == WatchlistItem.tmdb_id)
        .filter(WatchlistItem.user_id == user.user_id)
        .order_by(WatchlistItem.added_at.desc(), WatchlistItem.id.desc())
        .all()
    )
    return [_format_watchlist_item(item, movie) for item, movie in rows]


@router.post("/{tmdb_id}")
def add_to_watchlist(
    tmdb_id: int,
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    movie = _movie_or_404(db, tmdb_id)
    item = (
        db.query(WatchlistItem)
        .filter(
            WatchlistItem.user_id == user.user_id,
            WatchlistItem.tmdb_id == tmdb_id,
        )
        .first()
    )
    if item is None:
        item = WatchlistItem(user_id=user.user_id, tmdb_id=tmdb_id)
        db.add(item)
        db.commit()
        db.refresh(item)
    return {**_format_watchlist_item(item, movie), "in_watchlist": True}


@router.delete("/{tmdb_id}")
def remove_from_watchlist(
    tmdb_id: int,
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    item = (
        db.query(WatchlistItem)
        .filter(
            WatchlistItem.user_id == user.user_id,
            WatchlistItem.tmdb_id == tmdb_id,
        )
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Film not in your watchlist")
    db.delete(item)
    db.commit()
    return {"tmdb_id": tmdb_id, "deleted": True, "in_watchlist": False}
