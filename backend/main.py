import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from models.database import init_db
from routers import feedback, logs, movies, people, profile, reviews, watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("cinematch")

app = FastAPI(title="CineMatch API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    try:
        from models.database import SessionLocal
        from services.embeddings import embed_movies

        db = SessionLocal()
        try:
            stats = embed_movies(db)
            if stats["embedded"]:
                logger.info("Embeddings ready via %s (%d movies)", stats["provider"], stats["embedded"])
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Embedding warmup skipped: %s", exc)

    try:
        from models.database import SessionLocal
        from routers.deps import DEFAULT_USERNAME, normalize_username
        from services.letterboxd import backfill_watched_tmdb_ids, sync_local_letterboxd_history

        db = SessionLocal()
        try:
            username = normalize_username(DEFAULT_USERNAME)
            user = _get_or_create_user(db, username)
            result = sync_local_letterboxd_history(db, user_id=user.user_id)
            filled = backfill_watched_tmdb_ids(db)
            if result["watched_added"] or result["ratings_applied"] or filled:
                logger.info(
                    "Letterboxd history synced: %s (%d watched titles matched to catalog)",
                    result,
                    filled,
                )
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Could not sync local Letterboxd history: %s", exc)


def _get_or_create_user(db, username):
    from models.database import User

    user = db.query(User).filter(User.username == username).first()
    if not user:
        user = User(username=username)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


app.include_router(movies.router)
app.include_router(people.router)
app.include_router(logs.router)
app.include_router(reviews.router)
app.include_router(feedback.router)
app.include_router(profile.router)
app.include_router(watchlist.router)

try:
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
except Exception:
    pass


@app.get("/")
def root():
    return {"status": "CineMatch API running", "version": "2.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}
