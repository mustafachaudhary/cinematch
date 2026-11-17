from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import MovieCache, UserFeedback, get_db
from routers.deps import resolve_user
from services import feedback as feedback_svc

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    kind: Literal["hide", "not_interested"] = "not_interested"


class ImpressionRequest(BaseModel):
    tmdb_ids: list[int] = Field(..., min_length=1, max_length=100)
    session_id: str | None = None


def _movie_or_404(db: Session, tmdb_id: int) -> MovieCache:
    movie = db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail="Unknown film")
    return movie


@router.get("")
def list_feedback(
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    rows = (
        db.query(UserFeedback)
        .filter(UserFeedback.user_id == user.user_id)
        .order_by(UserFeedback.created_at.desc())
        .all()
    )
    return {
        "results": [
            {
                "tmdb_id": row.tmdb_id,
                "kind": row.kind,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/impressions")
def record_impressions(
    payload: ImpressionRequest,
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    known = {
        int(row[0])
        for row in db.query(MovieCache.tmdb_id)
        .filter(MovieCache.tmdb_id.in_(payload.tmdb_ids))
        .all()
    }
    recorded = feedback_svc.record_impressions(
        db,
        user.user_id,
        [tmdb_id for tmdb_id in payload.tmdb_ids if tmdb_id in known],
        session_id=payload.session_id,
    )
    return {"recorded": recorded}


@router.post("/{tmdb_id}")
def add_feedback(
    tmdb_id: int,
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    _movie_or_404(db, tmdb_id)
    row = feedback_svc.set_feedback(db, user.user_id, tmdb_id, payload.kind)
    return {"tmdb_id": tmdb_id, "kind": row.kind, "active": True}


@router.delete("/{tmdb_id}")
def remove_feedback(
    tmdb_id: int,
    kind: Literal["hide", "not_interested"] | None = Query(None),
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    deleted = feedback_svc.clear_feedback(db, user.user_id, tmdb_id, kind)
    if not deleted:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return {"tmdb_id": tmdb_id, "deleted": deleted}
