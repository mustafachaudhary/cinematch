import csv
import logging
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Header, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import (
    LoggedMovie,
    MovieCache,
    Review,
    User,
    UserProfile,
    WatchedMovie,
    get_db,
    hash_password,
    needs_rehash,
    verify_password,
)
from routers.deps import DEFAULT_USERNAME, normalize_username, resolve_user
from services import sentiment, tmdb
from services.letterboxd import backfill_watched_tmdb_ids, parse_rating, upsert_watched_row
from services.ratings import ensure_cached_movie, set_user_rating

logger = logging.getLogger("cinematch.profile")

router = APIRouter(prefix="/profile", tags=["profile"])

UPLOADS_DIR = Path("uploads/pfp")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Login throttling: per-username failure timestamps inside a sliding window.
# Tied to the account (not IP) per OWASP; in-memory is fine for a local app.
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = {}

# Precomputed hash so unknown users pay the same PBKDF2 cost (timing parity).
_dummy_hash: str | None = None


def _too_many_attempts(username: str) -> int:
    """Seconds remaining in lockout, or 0 when the name may attempt login."""
    now = time.monotonic()
    recent = [t for t in _login_failures.get(username, [])
              if now - t < LOGIN_WINDOW_SECONDS]
    _login_failures[username] = recent
    if len(recent) < LOGIN_MAX_FAILURES:
        return 0
    oldest = min(recent)
    return max(1, int(LOGIN_WINDOW_SECONDS - (now - oldest)))


def _register_failure(username: str) -> None:
    _login_failures.setdefault(username, []).append(time.monotonic())


def _clear_failures(username: str) -> None:
    _login_failures.pop(username, None)


class UpdateProfileRequest(BaseModel):
    username: str | None = None
    bio: str | None = None
    display_name: str | None = None
    favorite_movie_1: int | None = None
    favorite_movie_2: int | None = None
    favorite_movie_3: int | None = None
    favorite_movie_4: int | None = None


class SignupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


def find_profile(db: Session, username: str):
    return (
        db.query(UserProfile)
        .filter(UserProfile.username == normalize_username(username))
        .first()
    )


def get_or_create_profile(db: Session, username: str | None):
    """Profile for the given user; falls back to the first existing profile."""
    if username:
        username = normalize_username(username)
        if not db.query(User).filter(User.username == username).first():
            return None
        profile = find_profile(db, username)
        if not profile:
            profile = UserProfile(username=username)
            db.add(profile)
            db.commit()
            db.refresh(profile)
        return profile

    profile = db.query(UserProfile).first()
    if not profile:
        profile = UserProfile(username=DEFAULT_USERNAME)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.get("/exists")
def profile_exists(username: str = None, db: Session = Depends(get_db)):
    if not username:
        return {"exists": False}
    exists = (
        db.query(User)
        .filter(User.username == normalize_username(username))
        .first()
        is not None
    )
    return {"exists": exists}


@router.post("/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    username = normalize_username(req.username)
    # NIST 800-63B: length over composition rules; never silently alter the
    # password (no strip/truncate). Long passphrases stay allowed.
    password = req.password or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="Password must be at most 128 characters")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    profile = UserProfile(username=username)
    db.add(profile)
    db.commit()
    logger.info("Signup: new account '%s'", username)
    return {"username": username, "display_name": profile.display_name}


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    global _dummy_hash
    username = normalize_username(req.username)
    password = req.password or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    lock_remaining = _too_many_attempts(username)
    if lock_remaining:
        logger.warning("Login throttled for '%s' (%ds remaining)", username, lock_remaining)
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {lock_remaining}s.",
        )

    # Same work whether or not the account exists: unknown usernames still
    # run a PBKDF2 verification so response timing can't enumerate users.
    user = db.query(User).filter(User.username == username).first()
    stored_hash = user.password_hash if user else None
    if stored_hash is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password("timing-parity-placeholder")
        stored_hash = _dummy_hash

    ok = verify_password(password, stored_hash) and user is not None
    if not ok:
        _register_failure(username)
        logger.info("Login failed for '%s'", username)
        # Generic message + identical status code either way (OWASP).
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _clear_failures(username)

    # Transparently upgrade legacy unsalted hashes.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()

    profile = get_or_create_profile(db, username)
    logger.info("Login OK: '%s'", username)
    return {
        "id": profile.id,
        "username": profile.username,
        "display_name": profile.display_name,
        "bio": profile.bio,
        "pfp_path": profile.pfp_path,
    }


@router.get("/")
def get_profile(
    db: Session = Depends(get_db),
    x_username: str = Header(None, alias="X-Username"),
):
    profile = get_or_create_profile(db, x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")

    user_id_row = (
        db.query(User.user_id).filter(User.username == profile.username).first()
    )
    user_id = user_id_row[0] if user_id_row else None

    rated_count = (
        db.query(LoggedMovie).filter(LoggedMovie.user_id == user_id).count()
        if user_id else 0
    )
    watched_count = (
        db.query(WatchedMovie).filter(WatchedMovie.user_id == user_id).count()
        if user_id else 0
    )
    review_rows = (
        db.query(Review.sentiment_compound)
        .filter(Review.user_id == user_id)
        .all()
        if user_id else []
    )
    compounds = [float(c or 0.0) for (c,) in review_rows]
    sentiment_stats = sentiment.aggregate(
        [type("R", (), {"sentiment_compound": c, "content": ""})() for c in compounds]
    ) if compounds else {"count": 0, "avg_compound": 0.0,
                        "distribution": {"positive": 0, "mixed": 0, "negative": 0},
                        "most_positive": None, "most_negative": None}

    favorite_ids = [
        movie_id
        for movie_id in (
            profile.favorite_movie_1,
            profile.favorite_movie_2,
            profile.favorite_movie_3,
            profile.favorite_movie_4,
        )
        if movie_id
    ]
    favorite_rows = (
        db.query(MovieCache).filter(MovieCache.tmdb_id.in_(favorite_ids)).all()
        if favorite_ids else []
    )
    favorite_map = {movie.tmdb_id: movie for movie in favorite_rows}
    favorites = []
    for movie_id in favorite_ids:
        movie = favorite_map.get(movie_id)
        if not movie:
            continue
        favorites.append({
            "tmdb_id": movie.tmdb_id,
            "title": movie.title,
            "poster_path": movie.poster_path,
            "release_year": movie.release_year,
            "genres": movie.genres.split(",") if movie.genres else [],
        })

    return {
        "id": profile.id,
        "username": profile.username,
        "display_name": profile.display_name,
        "bio": profile.bio,
        "pfp_path": profile.pfp_path,
        "favorite_movie_1": profile.favorite_movie_1,
        "favorite_movie_2": profile.favorite_movie_2,
        "favorite_movie_3": profile.favorite_movie_3,
        "favorite_movie_4": profile.favorite_movie_4,
        "favorites": favorites,
        "stats": {
            "watched_count": watched_count,
            "rated_count": rated_count,
            "reviews_count": len(compounds),
            "avg_review_sentiment": round(
                sum(compounds) / len(compounds), 3
            ) if compounds else 0.0,
            "sentiment_distribution": sentiment_stats["distribution"],
        },
    }


@router.put("/")
def update_profile(
    req: UpdateProfileRequest,
    db: Session = Depends(get_db),
    x_username: str = Header(None, alias="X-Username"),
):
    profile = get_or_create_profile(db, x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")

    if req.username:
        new_name = normalize_username(req.username)
        if new_name and new_name != profile.username:
            if db.query(UserProfile).filter(UserProfile.username == new_name).first():
                raise HTTPException(status_code=400, detail="Username already taken")
            user = db.query(User).filter(User.username == profile.username).first()
            if user:
                user.username = new_name
            profile.username = new_name
    if req.display_name:
        profile.display_name = req.display_name
    if req.bio is not None:
        profile.bio = req.bio
    for field in ("favorite_movie_1", "favorite_movie_2", "favorite_movie_3", "favorite_movie_4"):
        value = getattr(req, field)
        if value:
            setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return get_profile(db=db, x_username=profile.username)


@router.post("/signout")
def signout(
    x_username: str = Header(None, alias="X-Username"),
):
    # Identity lives in the X-Username header; the client clears it. The
    # server keeps no session state and must not touch stored profile data.
    if x_username:
        logger.info("Sign out: '%s'", x_username)
    return {"ok": True}


@router.post("/upload-pfp")
def upload_pfp(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_username: str = Header(None, alias="X-Username"),
):
    profile = get_or_create_profile(db, x_username)
    if x_username and profile is None:
        raise HTTPException(status_code=404, detail="Username not found")

    max_size = 5 * 1024 * 1024
    min_size = 10 * 1024
    content = file.file.read()
    size = len(content)
    if size < min_size:
        raise HTTPException(status_code=400, detail=f"Image too small (min {min_size // 1024}KB)")
    if size > max_size:
        raise HTTPException(status_code=400, detail=f"Image too large (max {max_size >> 20}MB)")

    allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Use: {', '.join(sorted(allowed))}")

    filename = f"pfp_{profile.id}_{Path(file.filename).name}"
    filepath = UPLOADS_DIR / filename
    try:
        filepath.write_bytes(content)
        profile.pfp_path = f"/uploads/pfp/{filename}"
        db.commit()
        return {"pfp_path": profile.pfp_path, "size": size}
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        file.file.close()


@router.post("/import-letterboxd")
async def import_letterboxd_zip(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    """Import ratings.csv + watched.csv from a Letterboxd export zip.

    Every rated film is resolved against TMDB, cataloged with an embedding,
    and written into both the display logs and the CF rating matrix.
    """
    imported = skipped = watched_imported = 0
    errors: list[str] = []

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_zip = Path(tmpdir) / (file.filename or "export.zip")
            with open(tmp_zip, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            ratings_csv = watched_csv = None
            try:
                zf = zipfile.ZipFile(tmp_zip)
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="Invalid zip file")

            with zf:
                for name in zf.namelist():
                    if name.endswith("ratings.csv"):
                        ratings_csv = zf.read(name).decode("utf-8").splitlines()
                    elif name.endswith("watched.csv"):
                        watched_csv = zf.read(name).decode("utf-8").splitlines()

            if not ratings_csv:
                raise HTTPException(status_code=400, detail="ratings.csv not found in zip")

            if watched_csv:
                for row in csv.DictReader(watched_csv):
                    if upsert_watched_row(db, row, user.user_id):
                        watched_imported += 1
                db.commit()

            for row_num, row in enumerate(csv.DictReader(ratings_csv), start=1):
                title = (row.get("Name") or "").strip()
                year_raw = (row.get("Year") or "").strip()
                rating = parse_rating(row.get("Rating"))
                if not title or rating is None:
                    skipped += 1
                    continue
                year = int(year_raw) if year_raw.isdigit() else None

                # Real rating dates power time-decay; Letterboxd exports a
                # ``Date`` column ("2024-03-05") when the user opted into
                # activity export. Absent or unparseable dates fall back to
                # the default "now" stamp.
                logged_at = None
                date_raw = (row.get("Date") or "").strip()
                if date_raw:
                    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                        try:
                            logged_at = datetime.strptime(date_raw.split()[0], fmt)
                            break
                        except ValueError:
                            continue

                try:
                    results = tmdb.search_movies(title, year=year, limit=5)
                    if not results:
                        skipped += 1
                        errors.append(f"Row {row_num}: '{title}' not found on TMDB")
                        continue

                    movie_data = results[0]
                    if year and len(results) > 1:
                        for candidate in results:
                            if candidate["release_year"] == year:
                                movie_data = candidate
                                break

                    ensure_cached_movie(db, movie_data)
                    set_user_rating(
                        db, user.user_id, movie_data["tmdb_id"], rating,
                        commit=False, logged_at=logged_at,
                    )
                    upsert_watched_row(
                        db, row, user.user_id, rating=rating, tmdb_id=movie_data["tmdb_id"]
                    )
                    db.commit()
                    imported += 1
                    if row_num % 10 == 0:
                        logger.info("Letterboxd import progress: %d rows", row_num)
                except Exception as exc:
                    db.rollback()
                    skipped += 1
                    errors.append(f"Row {row_num} ('{title}'): {exc}")

            # Watched-but-unrated rows have no TMDB resolution pass; fill
            # their ids from the (now wider) catalog so the feed can exclude
            # them for this user.
            backfill_watched_tmdb_ids(db, user.user_id)

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Letterboxd import failed")
        raise HTTPException(status_code=500, detail=f"Failed to process zip: {exc}")
    finally:
        file.file.close()

    return {
        "imported": imported,
        "watched_imported": watched_imported,
        "skipped": skipped,
        "total_processed": imported + skipped,
        "errors": errors[:10],
    }
