from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import MovieCache, Review, get_db
from routers.deps import resolve_user
from services import sentiment

router = APIRouter(prefix="/reviews", tags=["reviews"])


class ReviewRequest(BaseModel):
    content: str = Field(..., min_length=3, max_length=2000)


class ScoreRequest(BaseModel):
    content: str = Field(..., max_length=2000)


@router.post("/score")
def score_review(req: ScoreRequest):
    """Score text without saving — powers the live vibe meter while typing."""
    if not req.content.strip():
        return {"compound": 0.0, "pos": 0.0, "neu": 1.0, "neg": 0.0, "label": "neutral"}
    scores = sentiment.analyze(req.content)
    return {**scores, "label": sentiment.label(scores["compound"])}


def format_review(review: Review) -> dict:
    return {
        "id": review.id,
        "tmdb_id": review.tmdb_id,
        "content": review.content,
        "sentiment": {
            "compound": round(review.sentiment_compound, 3),
            "pos": round(review.sentiment_pos, 3),
            "neu": round(review.sentiment_neu, 3),
            "neg": round(review.sentiment_neg, 3),
            "label": sentiment.label(review.sentiment_compound),
        },
        "created_at": review.created_at.isoformat() if review.created_at else None,
    }


@router.post("/{tmdb_id}")
def create_review(
    tmdb_id: int,
    req: ReviewRequest,
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    if not db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).first():
        raise HTTPException(status_code=404, detail="Unknown movie")

    scores = sentiment.analyze(req.content)
    review = (
        db.query(Review)
        .filter(Review.user_id == user.user_id, Review.tmdb_id == tmdb_id)
        .first()
    )
    if review:
        review.content = req.content.strip()
        review.sentiment_compound = scores["compound"]
        review.sentiment_pos = scores["pos"]
        review.sentiment_neu = scores["neu"]
        review.sentiment_neg = scores["neg"]
    else:
        review = Review(
            user_id=user.user_id,
            tmdb_id=tmdb_id,
            content=req.content.strip(),
            sentiment_compound=scores["compound"],
            sentiment_pos=scores["pos"],
            sentiment_neu=scores["neu"],
            sentiment_neg=scores["neg"],
        )
        db.add(review)
    db.commit()
    db.refresh(review)
    return format_review(review)


@router.get("/")
def my_reviews(db: Session = Depends(get_db), user=Depends(resolve_user)):
    reviews = (
        db.query(Review)
        .filter(Review.user_id == user.user_id)
        .order_by(Review.created_at.desc())
        .all()
    )
    tmdb_ids = {r.tmdb_id for r in reviews}
    titles = {}
    if tmdb_ids:
        rows = db.query(MovieCache.tmdb_id, MovieCache.title).filter(
            MovieCache.tmdb_id.in_(tmdb_ids)
        ).all()
        titles = dict(rows)

    payload = []
    for r in reviews:
        item = format_review(r)
        item["title"] = titles.get(r.tmdb_id, f"Film #{r.tmdb_id}")
        payload.append(item)

    return {
        "reviews": payload,
        "summary": sentiment.aggregate(reviews),
    }


@router.delete("/{tmdb_id}")
def delete_review(tmdb_id: int, db: Session = Depends(get_db), user=Depends(resolve_user)):
    review = (
        db.query(Review)
        .filter(Review.user_id == user.user_id, Review.tmdb_id == tmdb_id)
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="No review for this movie")
    db.delete(review)
    db.commit()
    return {"deleted": tmdb_id}
