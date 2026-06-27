from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.database import get_db, LoggedMovie, MovieCache
from services import tmdb, nlp

router = APIRouter(prefix="/logs", tags=["logs"])


class LogMovieRequest(BaseModel):
    tmdb_id: int
    rating: float


def format_logged_movie(movie_orm):
    """Convert logged movie ORM to JSON."""
    return {
        "id": movie_orm.id,
        "tmdb_id": movie_orm.tmdb_id,
        "title": movie_orm.title,
        "poster_path": movie_orm.poster_path,
        "genres": movie_orm.genres.split(",") if movie_orm.genres else [],
        "release_year": movie_orm.release_year,
        "rating": movie_orm.rating,
        "logged_at": movie_orm.logged_at.isoformat(),
    }


@router.get("/")
def get_logs(db: Session = Depends(get_db)):
    """Get all logged movies."""
    logs = db.query(LoggedMovie).order_by(LoggedMovie.logged_at.desc()).all()
    return [format_logged_movie(m) for m in logs]


@router.post("/")
def log_movie(req: LogMovieRequest, db: Session = Depends(get_db)):
    """Log a movie with a rating."""
    # Validate rating to make sure it complies with 1 to 5 star rule
    if not (1.0 <= req.rating <= 5.0):
        raise HTTPException(status_code=400, detail="Rating must be between 1.0 and 5.0 stars")
    
    # Check if already logged
    existing = db.query(LoggedMovie).filter(LoggedMovie.tmdb_id == req.tmdb_id).first()
    if existing:
        existing.rating = req.rating
        db.commit()
        return format_logged_movie(existing)
    
    # Check cache
    cached = db.query(MovieCache).filter(MovieCache.tmdb_id == req.tmdb_id).first()
    
    if cached:
        movie_data = {
            "tmdb_id": cached.tmdb_id,
            "title": cached.title,
            "overview": cached.overview,
            "genres": cached.genres.split(",") if cached.genres else [],
            "poster_path": cached.poster_path,
            "release_year": cached.release_year,
        }
        embedding = cached.embedding
    else:
        # Fetch from TMDB
        movie_data = tmdb.get_movie_details(req.tmdb_id)
        if not movie_data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Generate embedding
        embedding_list = nlp.embed_movie(movie_data)
        embedding = nlp.embedding_to_json(embedding_list)
    
    # Create logged movie
    logged = LoggedMovie(
        tmdb_id=req.tmdb_id,
        title=movie_data["title"],
        overview=movie_data["overview"],
        genres=",".join(movie_data["genres"]),
        poster_path=movie_data["poster_path"],
        release_year=movie_data["release_year"],
        rating=req.rating,
        embedding=embedding,
    )
    db.add(logged)
    db.commit()
    db.refresh(logged)
    
    return format_logged_movie(logged)


@router.delete("/{tmdb_id}")
def remove_log(tmdb_id: int, db: Session = Depends(get_db)):
    """Remove a logged movie."""
    logged = db.query(LoggedMovie).filter(LoggedMovie.tmdb_id == tmdb_id).first()
    if not logged:
        raise HTTPException(status_code=404, detail="Movie not found in logs")
    
    db.delete(logged)
    db.commit()
    
    return {"deleted": tmdb_id}
