"""Explicit feedback and feed-impression bookkeeping.

Suppression is intentionally simple: a hidden / not-interested film is removed
from that user's feed candidates, then the ranker decides the rest. This keeps
feedback reversible and avoids invisible score hacks.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from models.database import FeedImpression, UserFeedback

SUPPRESS_KINDS = {"hide", "not_interested"}
IMPRESSION_RETENTION_DAYS = 90


def suppressed_ids(db, user_id: int) -> set[int]:
    rows = (
        db.query(UserFeedback.tmdb_id)
        .filter(
            UserFeedback.user_id == user_id,
            UserFeedback.kind.in_(tuple(SUPPRESS_KINDS)),
        )
        .distinct()
        .all()
    )
    return {int(row[0]) for row in rows}


def set_feedback(db, user_id: int, tmdb_id: int, kind: str):
    if kind not in SUPPRESS_KINDS:
        raise ValueError(f"unsupported feedback kind: {kind}")
    row = (
        db.query(UserFeedback)
        .filter(
            UserFeedback.user_id == user_id,
            UserFeedback.tmdb_id == tmdb_id,
            UserFeedback.kind == kind,
        )
        .first()
    )
    if row is None:
        row = UserFeedback(user_id=user_id, tmdb_id=tmdb_id, kind=kind)
        db.add(row)
    else:
        row.created_at = datetime.utcnow()
    db.commit()
    return row


def clear_feedback(db, user_id: int, tmdb_id: int, kind: str | None = None) -> int:
    query = db.query(UserFeedback).filter(
        UserFeedback.user_id == user_id, UserFeedback.tmdb_id == tmdb_id
    )
    if kind:
        query = query.filter(UserFeedback.kind == kind)
    deleted = query.delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


def record_impressions(db, user_id: int, tmdb_ids, session_id: str | None = None) -> int:
    tmdb_ids = [int(tmdb_id) for tmdb_id in dict.fromkeys(tmdb_ids)]
    if not tmdb_ids:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=IMPRESSION_RETENTION_DAYS)
    db.query(FeedImpression).filter(FeedImpression.shown_at < cutoff).delete(
        synchronize_session=False
    )
    if session_id:
        recent = datetime.utcnow() - timedelta(hours=6)
        seen = {
            int(row[0])
            for row in db.query(FeedImpression.tmdb_id)
            .filter(
                FeedImpression.user_id == user_id,
                FeedImpression.session_id == session_id,
                FeedImpression.shown_at >= recent,
            )
            .all()
        }
        tmdb_ids = [tmdb_id for tmdb_id in tmdb_ids if tmdb_id not in seen]
        if not tmdb_ids:
            db.commit()
            return 0
    db.add_all(
        [
            FeedImpression(user_id=user_id, tmdb_id=tmdb_id, session_id=session_id)
            for tmdb_id in tmdb_ids
        ]
    )
    db.commit()
    return len(tmdb_ids)
