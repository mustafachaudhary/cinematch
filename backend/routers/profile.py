from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import os
import shutil
import zipfile
import csv
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from models.database import get_db, UserProfile, LoggedMovie, MovieCache
from services import tmdb, nlp
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
    imported = 0
    skipped = 0
    errors = []
    
    try:
        # Save the uploaded file to a temporary location
        # This is necessary because zipfile.ZipFile expects a file path or a seekable file-like object
        # UploadFile.file is not always seekable directly.
        with tempfile.TemporaryDirectory() as tmpdir_name:
            tmpdir = Path(tmpdir_name)
            temp_zip_path = tmpdir / file.filename
            
            with open(temp_zip_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            ratings_csv_found = False
            ratings_file_content = None

            with zipfile.ZipFile(temp_zip_path, 'r') as z:
                # Iterate through all files in the zip to find 'ratings.csv'
                for name in z.namelist():
                    if name.endswith('ratings.csv'):
                        # Found ratings.csv, open it directly from the zip
                        with z.open(name, 'r') as csv_file_in_zip:
                            # Read the content into memory as csv.DictReader needs a seekable object if not bytes
                            # and we need to ensure utf-8 decoding.
                            ratings_file_content = csv_file_in_zip.read().decode('utf-8').splitlines()
                        ratings_csv_found = True
                        break

            if not ratings_csv_found or not ratings_file_content:
                raise HTTPException(status_code=400, detail="ratings.csv not found in the uploaded zip file.")
            
            # Read and import
            reader = csv.DictReader(ratings_file_content)
            row_num = 0
            for row in reader:
                row_num += 1
                title = "Unknown Title" # Default for error messages
                try:
                    title = row.get('Name', '').strip()
                    year_str = row.get('Year', '').strip()
                    rating_str = row.get('Rating', '').strip()
                    
                    if not title:
                        print(f"⚠️  Skipping row {row_num}: No movie title found.")
                        skipped += 1
                        continue
                    
                    # Parse year
                    year = None
                    if year_str and year_str.isdigit():
                        year = int(year_str)
                    
                    # Parse rating (Letterboxd scale: 0-5, we use 0.5-5.0)
                    if not rating_str or rating_str == '':
                        print(f"⚠️  Skipping '{title}' (row {row_num}): No rating found.")
                        skipped += 1
                        continue
                    
                    rating = float(rating_str)
                    
                    # Letterboxd: 0 means not rated, 0.5-5.0 are actual ratings
                    if rating == 0:
                        print(f"⚠️  Skipping '{title}' (row {row_num}): Rating is 0 (not rated).")
                        skipped += 1
                        continue
                    if rating < 0.5 or rating > 5.0: # Ensure valid rating range
                        print(f"⚠️  Skipping '{title}' (row {row_num}): Invalid rating value '{rating}'.")
                        skipped += 1
                        continue
                    
                    # Check if already logged by TMDB ID or title
                    # It's more robust to search TMDB first and then check by TMDB_ID.
                    
                    # Search TMDB for the movie, prioritizing year if available
                    print(f"🔍 Searching TMDB for: '{title}' (Year: {year_str if year else 'N/A'})")
                    results = tmdb.search_movies(title, year=year, limit=5) # Pass year for better accuracy
                    
                    movie_data = None
                    if not results:
                        print(f"  ✗ TMDB not found for '{title}' (Year: {year_str if year else 'N/A'}), skipping.")
                        skipped += 1
                        continue
                    
                    # Try to match year if available and multiple results
                    movie_data = results[0] # Default to first result
                    if year and len(results) > 1:
                        for r in results:
                            r_year = r.get('release_date', '')
                            if r_year and r_year.startswith(str(year)):
                                movie_data = r
                                break
                    
                    tmdb_id = movie_data['tmdb_id']
                    
                    # Check if already logged by TMDB ID
                    existing_logged_movie = db.query(LoggedMovie).filter(
                        LoggedMovie.tmdb_id == tmdb_id
                    ).first()
                    if existing_logged_movie:
                        print(f"✓ Already logged: {movie_data['title']} (TMDB ID: {tmdb_id}), skipping.")
                        skipped += 1
                        continue
                    
                    # Check if cached
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
                        # No commit here yet, as it's part of a larger transaction
                        # Or, commit if you want to ensure cache is written even if LoggedMovie fails
                        # For now, let's keep it consistent with existing pattern and commit after logged.
                    
                    # Log the movie with rating
                    logged = LoggedMovie(
                        tmdb_id=tmdb_id,
                        title=movie_data['title'],
                        overview=movie_data['overview'],
                        genres=",".join(movie_data['genres']),
                        poster_path=movie_data['poster_path'],
                        release_year=movie_data['release_year'],
                        rating=rating,
                        embedding=nlp.embedding_to_json(nlp.embed_movie(movie_data)) # Re-embeds, could use cached embedding
                    )
                    db.add(logged)
                    db.commit() # Commit after both cache (if new) and logged movie are added
                    
                    imported += 1
                    print(f"  ✅ Logged: {movie_data['title']} ({rating}★)")
                    
                except Exception as e:
                    skipped += 1
                    error_msg = f"Row {row_num} ('{title}'): {str(e)}"
                    errors.append(error_msg)
                    if len(errors) <= 5: # Only print first few errors to avoid log spam
                        print(f"❌ Error on row {row_num} ('{title}'): {str(e)}")
            
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file: Not a valid ZIP archive.")
    except Exception as e: # Catch other potential file/processing errors
        print(f"Critical error during import: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process zip file: {str(e)}")
    finally:
        # The temporary directory handles cleanup
        file.file.close() # Ensure the uploaded file stream is closed
    
    final_summary = f"\n📊 Import Summary: {imported} movies imported, {skipped} skipped."
    if errors:
        final_summary += f" First {min(len(errors), 5)} errors: {', '.join(errors[:5])}"
    print(final_summary)
    
    return {"imported": imported, "skipped": skipped, "total_processed": imported + skipped, "errors": errors[:5]}
