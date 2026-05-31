from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from models.database import get_db, UserProfile, LoggedMovie, MovieCache
from services import tmdb
import os
import shutil
from pathlib import Path
from pydantic import BaseModel

router = APIRouter(prefix="/profile", tags=["profile"])

UPLOADS_DIR = Path("uploads/pfp")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


class UpdateProfileRequest(BaseModel):
    username: str = None
    bio: str = None
    favorite_movie_1: int = None
    favorite_movie_2: int = None
    favorite_movie_3: int = None
    favorite_movie_4: int = None


def get_or_create_profile(db: Session):
    """Get existing profile or create default one."""
    profile = db.query(UserProfile).first()
    if not profile:
        profile = UserProfile(username="Cinephile")
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.get("/")
def get_profile(db: Session = Depends(get_db)):
    """Get user profile."""
    profile = get_or_create_profile(db)
    return {
        "id": profile.id,
        "username": profile.username,
        "bio": profile.bio,
        "pfp_path": profile.pfp_path,
        "favorite_movie_1": profile.favorite_movie_1,
        "favorite_movie_2": profile.favorite_movie_2,
        "favorite_movie_3": profile.favorite_movie_3,
        "favorite_movie_4": profile.favorite_movie_4,
    }


@router.put("/")
def update_profile(req: UpdateProfileRequest, db: Session = Depends(get_db)):
    """Update user profile."""
    profile = get_or_create_profile(db)
    
    if req.username:
        profile.username = req.username
    if req.bio:
        profile.bio = req.bio
    if req.favorite_movie_1:
        profile.favorite_movie_1 = req.favorite_movie_1
    if req.favorite_movie_2:
        profile.favorite_movie_2 = req.favorite_movie_2
    if req.favorite_movie_3:
        profile.favorite_movie_3 = req.favorite_movie_3
    if req.favorite_movie_4:
        profile.favorite_movie_4 = req.favorite_movie_4
    
    db.commit()
    db.refresh(profile)
    
    return get_profile(db)


@router.post("/upload-pfp")
def upload_pfp(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload profile picture with size constraints."""
    profile = get_or_create_profile(db)
    
    # Check file size: 100KB - 5MB
    MAX_SIZE = 5 * 1024 * 1024  # 5MB
    MIN_SIZE = 10 * 1024  # 10KB
    
    file_content = file.file.read()
    file_size = len(file_content)
    
    if file_size < MIN_SIZE:
        raise HTTPException(status_code=400, detail=f"Image too small (min {MIN_SIZE//1024}KB)")
    if file_size > MAX_SIZE:
        raise HTTPException(status_code=400, detail=f"Image too large (max {MAX_SIZE//(1024*1024)}MB)")
    
    # Check file extension
    allowed_ext = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_ext:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Use: {', '.join(allowed_ext)}")
    
    # Save file
    filename = f"pfp_{profile.id}_{file.filename}"
    filepath = UPLOADS_DIR / filename
    
    try:
        with open(filepath, "wb") as f:
            f.write(file_content)
        
        profile.pfp_path = f"/uploads/pfp/{filename}"
        db.commit()
        db.refresh(profile)
        
        print(f"✅ PFP uploaded: {profile.pfp_path} ({file_size//1024}KB)")
        return {"pfp_path": profile.pfp_path, "size": file_size}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        file.file.close()


@router.post("/import-letterboxd")
def import_letterboxd_zip(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import Letterboxd data from zip file."""
    import zipfile
    import csv
    import tempfile
    
    imported = 0
    skipped = 0
    errors = []
    
    try:
        # Extract zip to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(file.file, 'r') as z:
                z.extractall(tmpdir)
            
            # Find ratings.csv
            ratings_path = Path(tmpdir) / "ratings.csv"
            if not ratings_path.exists():
                raise HTTPException(status_code=400, detail="ratings.csv not found in zip")
            
            # Read and import
            with open(ratings_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                row_num = 0
                for row in reader:
                    row_num += 1
                    try:
                        title = row.get('Name', '').strip()
                        year_str = row.get('Year', '').strip()
                        rating_str = row.get('Rating', '').strip()
                        
                        if not title:
                            skipped += 1
                            continue
                        
                        # Parse year
                        year = None
                        if year_str and year_str.isdigit():
                            year = int(year_str)
                        
                        # Parse rating (Letterboxd scale: 0-5, but we use 1-5)
                        if not rating_str or rating_str == '':
                            skipped += 1
                            continue
                        
                        rating = float(rating_str)
                        # Letterboxd: 0-5, convert if needed
                        if rating == 0:  # Not rated
                            skipped += 1
                            continue
                        if rating < 0 or rating > 5.0:
                            skipped += 1
                            continue
                        
                        # Check if already logged
                        existing = db.query(LoggedMovie).filter(
                            LoggedMovie.title == title
                        ).first()
                        if existing:
                            skipped += 1
                            continue
                        
                        # Search TMDB
                        results = tmdb.search_movies(title, limit=5)
                        if not results:
                            skipped += 1
                            continue
                        
                        movie_data = results[0]
                        if year and len(results) > 1:
                            for r in results:
                                release = r.get('release_date', '')
                                if release and release.startswith(str(year)):
                                    movie_data = r
                                    break
                        
                        tmdb_id = movie_data['tmdb_id']
                        
                        # Cache if needed
                        from services import nlp
                        cached = db.query(MovieCache).filter(
                            MovieCache.tmdb_id == tmdb_id
                        ).first()
                        if not cached:
                            embedding_list = nlp.embed_movie(movie_data)
                            cached = MovieCache(
                                tmdb_id=tmdb_id,
                                title=movie_data['title'],
                                overview=movie_data['overview'],
                                genres=",".join(movie_data['genres']),
                                poster_path=movie_data['poster_path'],
                                release_year=movie_data['release_year'],
                                popularity=movie_data['popularity'],
                                vote_average=movie_data['vote_average'],
                                embedding=nlp.embedding_to_json(embedding_list),
                            )
                            db.add(cached)
                            db.commit()
                        
                        # Log the movie
                        logged = LoggedMovie(
                            tmdb_id=tmdb_id,
                            title=movie_data['title'],
                            overview=movie_data['overview'],
                            genres=",".join(movie_data['genres']),
                            poster_path=movie_data['poster_path'],
                            release_year=movie_data['release_year'],
                            rating=rating,
                            embedding=nlp.embedding_to_json(nlp.embed_movie(movie_data))
                        )
                        db.add(logged)
                        db.commit()
                        
                        imported += 1
                        print(f"✅ Imported: {title} ({year}) - {rating}★")
                    except Exception as e:
                        skipped += 1
                        errors.append(f"Row {row_num}: {str(e)[:100]}")
                        if len(errors) <= 5:
                            print(f"❌ Error on row {row_num}: {str(e)}")
    
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    finally:
        file.file.close()
    
    print(f"\n📊 Import Summary: {imported} imported, {skipped} skipped")
    if errors:
        print(f"First errors: {errors[:3]}")
    
    return {"imported": imported, "skipped": skipped, "total_processed": imported + skipped, "errors": errors[:5]}
