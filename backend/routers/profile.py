from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
import os
import shutil
import zipfile
import csv
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session
from models.database import User, get_db, UserProfile, LoggedMovie, MovieCache, WatchedMovie
from services import tmdb, nlp
from services.letterboxd import upsert_watched_row, parse_rating
from pydantic import BaseModel
import hashlib

router = APIRouter(prefix="/profile", tags=["profile"])

UPLOADS_DIR = Path("uploads/pfp")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


class UpdateProfileRequest(BaseModel):
    username: str = None
    bio: str = None
    display_name: str = None
    favorite_movie_1: int = None
    favorite_movie_2: int = None
    favorite_movie_3: int = None
    favorite_movie_4: int = None


class SignupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


def normalize_username(value: str):
    normalized = (value or "").strip()
    if normalized.startswith("@"):
        normalized = normalized.lstrip("@")
    return normalized.replace(" ", "").lower()


def find_profile_by_username(db: Session, username: str):
    if not username:
        return None
    username = normalize_username(username)
    return db.query(UserProfile).filter(UserProfile.username == username).first()


def get_or_create_profile(db: Session, username: str = None):
    """Get profile by username (if provided) or create default one."""
    if username:
        username = normalize_username(username)
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return None
        profile = find_profile_by_username(db, username)
        if not profile:
            profile = UserProfile(username=username)
            db.add(profile)
            db.commit()
            db.refresh(profile)
        return profile

    # Fallback to any existing profile
    profile = db.query(UserProfile).first()
    if not profile:
        profile = UserProfile(username="Cinephile")
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == (password_hash or '')


@router.get("/exists")
def profile_exists(username: str = None, db: Session = Depends(get_db)):
    username = normalize_username(username)
    if not username:
        return {"exists": False}
    exists = db.query(User).filter(User.username == username).first() is not None
    return {"exists": exists}


@router.post("/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    username = normalize_username(req.username)
    password = (req.password or '').strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    profile = UserProfile(username=username)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return {
        "username": username,
        "display_name": None,
    }


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    username = normalize_username(req.username)
    password = (req.password or '').strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Username not found")
    if not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

    return get_profile(db, x_username=username)


@router.get("/")
def get_profile(db: Session = Depends(get_db), x_username: str = Header(None, alias='X-Username')):
    """Get user profile."""
    profile = get_or_create_profile(db, username=x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")

    watched_count = db.query(WatchedMovie).count()
    rated_count = db.query(LoggedMovie).count()
    return {
        "id": profile.id,
        "username": profile.username,
        "display_name": getattr(profile, 'display_name', None),
        "bio": profile.bio,
        "pfp_path": profile.pfp_path,
        "favorite_movie_1": profile.favorite_movie_1,
        "favorite_movie_2": profile.favorite_movie_2,
        "favorite_movie_3": profile.favorite_movie_3,
        "favorite_movie_4": profile.favorite_movie_4,
        "stats": {
            "watched_count": watched_count or rated_count,
            "rated_count": rated_count,
        },
    }


@router.put("/")
def update_profile(req: UpdateProfileRequest, db: Session = Depends(get_db), x_username: str = Header(None, alias='X-Username')):
    """Update user profile."""
    profile = get_or_create_profile(db, username=x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")
    
    if req.username:
        profile.username = req.username
    # Support display_name in update payload if present
    if hasattr(req, 'display_name') and req.display_name:
        try:
            profile.display_name = req.display_name
        except Exception:
            pass
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


@router.post("/signout")
def signout(db: Session = Depends(get_db), x_username: str = Header(None, alias='X-Username')):
    """Reset the local profile to defaults (simple sign-out).
    This is a lightweight, non-auth sign-out that clears PFP and favorites.
    """
    profile = get_or_create_profile(db, username=x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")
    profile.username = "Cinephile"
    profile.bio = ""
    profile.pfp_path = None
    profile.favorite_movie_1 = None
    profile.favorite_movie_2 = None
    profile.favorite_movie_3 = None
    profile.favorite_movie_4 = None
    db.commit()
    db.refresh(profile)
    return get_profile(db)


@router.post("/upload-pfp")
def upload_pfp(file: UploadFile = File(...), db: Session = Depends(get_db), x_username: str = Header(None, alias='X-Username')):
    """Upload profile picture with size constraints."""
    profile = get_or_create_profile(db, username=x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")
    
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
            watched_file_content = None

            with zipfile.ZipFile(temp_zip_path, 'r') as z:
                for name in z.namelist():
                    if name.endswith('ratings.csv'):
                        with z.open(name, 'r') as csv_file_in_zip:
                            ratings_file_content = csv_file_in_zip.read().decode('utf-8').splitlines()
                        ratings_csv_found = True
                    elif name.endswith('watched.csv'):
                        with z.open(name, 'r') as csv_file_in_zip:
                            watched_file_content = csv_file_in_zip.read().decode('utf-8').splitlines()

            if not ratings_csv_found or not ratings_file_content:
                raise HTTPException(status_code=400, detail="ratings.csv not found in the uploaded zip file.")

            watched_imported = 0
            if watched_file_content:
                for watched_row in csv.DictReader(watched_file_content):
                    if upsert_watched_row(db, watched_row):
                        watched_imported += 1
            
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

                    upsert_watched_row(db, row, rating=parse_rating(rating_str))
                    
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
                    
                    embedding_list = None
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
                    else:
                        # If cached, we still generate embedding for LoggedMovie
                        embedding_list = nlp.embed_movie(movie_data)

                    # Log the movie with rating
                    logged = LoggedMovie(
                        tmdb_id=tmdb_id,
                        title=movie_data['title'],
                        overview=movie_data['overview'],
                        genres=",".join(movie_data['genres']),
                        poster_path=movie_data['poster_path'],
                        release_year=movie_data['release_year'],
                        rating=rating,
                        embedding=nlp.embedding_to_json(embedding_list)
                    )
                    db.add(logged)

                    # Commit per-row to avoid a single UNIQUE failure aborting the whole batch
                    try:
                        db.commit()
                        db.refresh(logged)
                        if cached:
                            try:
                                db.refresh(cached)
                            except Exception:
                                pass
                        imported += 1
                        print(f"  ✅ Logged: {movie_data['title']} ({rating}★)")
                    except Exception as e:
                        db.rollback()
                        skipped += 1
                        error_msg = f"Row {row_num} ('{title}'): {str(e)}"
                        errors.append(error_msg)
                        print(f"❌ Error on row {row_num} ('{title}'): {str(e)}")
                        continue
                    
                except Exception as e:
                    db.rollback() # Rollback changes for the current row on error
                    skipped += 1
                    error_msg = f"Row {row_num} ('{title}'): {str(e)}"
                    errors.append(error_msg)
                    if len(errors) <= 5: # Only print first few errors to avoid log spam
                        print(f"❌ Error on row {row_num} ('{title}'): {str(e)}")
            
            # Commit all remaining changes after the loop (watched rows, etc.)
            if imported > 0:
                try:
                    db.commit()
                except Exception as e:
                    db.rollback()
                    err = f"❌ Error committing batch: {str(e)}"
                    print(err)
                    errors.append(err)
                    # Do not raise – continue and return partial results so import doesn't crash the server

    except zipfile.BadZipFile:
        db.rollback() # Rollback any pending transactions if the zip file itself is bad
        raise HTTPException(status_code=400, detail="Invalid zip file: Not a valid ZIP archive.")
    except Exception as e: # Catch other potential file/processing errors
        db.rollback() # Rollback on critical errors
        print(f"Critical error during import: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process zip file: {str(e)}")
    finally:
        # The temporary directory handles cleanup
        file.file.close() # Ensure the uploaded file stream is closed
    
    final_summary = f"\n📊 Import Summary: {imported} movies imported, {skipped} skipped."
    if errors:
        final_summary += f" First {min(len(errors), 5)} errors: {', '.join(errors[:5])}"
    print(final_summary)
    
    return {
        "imported": imported,
        "watched_imported": locals().get("watched_imported", 0),
        "skipped": skipped,
        "total_processed": imported + skipped,
        "errors": errors[:5],
    }
