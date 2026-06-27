from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging

# Try to import optional, heavier application modules. If any import fails
# (for example because numpy/scikit-learn/sentence-transformers are not
# installed yet), fall back to a limited-mode API so the frontend can
# load and basic health checks work while full dependencies are installed.
HAS_FULL_BACKEND = True
try:
    from models.database import init_db
    from routers import movies, logs, profile
except Exception as exc:  # pragma: no cover - runtime fallback
    logging.warning("Starting backend in limited mode; missing dependencies: %s", exc)
    init_db = None
    movies = None
    logs = None
    profile = None
    HAS_FULL_BACKEND = False


app = FastAPI(title="CineMatch API", version="1.0.0")

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


if init_db:
    @app.on_event("startup")
    def startup():
        init_db()
        try:
            from models.database import SessionLocal
            from services.letterboxd import sync_local_letterboxd_history

            db = SessionLocal()
            try:
                sync_local_letterboxd_history(db)
            finally:
                db.close()
        except Exception as exc:
            logging.warning("Could not sync local Letterboxd history: %s", exc)


if HAS_FULL_BACKEND and movies and logs and profile:
    app.include_router(movies.router)
    app.include_router(logs.router)
    app.include_router(profile.router)
else:
    # Minimal placeholder routers so the app can start without full deps.
    placeholder = APIRouter()

    @placeholder.get("/status")
    def limited_status():
        return {
            "status": "limited",
            "message": "Backend running in limited mode; install full dependencies to enable all features",
        }

    app.include_router(placeholder)

# Serve uploaded files (no-op if directory missing)
try:
    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
except Exception:
    # ignore static mount errors in limited environments
    pass


@app.get("/")
def root():
    return {"status": "CineMatch API running", "mode": ("full" if HAS_FULL_BACKEND else "limited")}
