from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, UniqueConstraint, ForeignKey, LargeBinary, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

# Load .env from the backend folder (ensure we pick the backend/.env regardless of cwd)
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(dotenv_path)

# Default to backend-local sqlite file; convert relative sqlite paths to absolute
raw_db_url = os.getenv("DATABASE_URL", "sqlite:///./cinematch.db")
if raw_db_url.startswith("sqlite:///./"):
    # path relative to backend folder
    rel = raw_db_url.replace("sqlite:///./", "")
    abs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), rel)
    DATABASE_URL = f"sqlite:///{abs_path}"
else:
    DATABASE_URL = raw_db_url
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class MovieCache(Base):
    __tablename__ = "movie_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True)
    title = Column(String)
    overview = Column(Text)
    genres = Column(String)  # comma-separated
    poster_path = Column(String, nullable=True)
    release_year = Column(Integer, nullable=True)
    popularity = Column(Float)
    vote_average = Column(Float)
    embedding = Column(Text)  # JSON-serialized list
    cached_at = Column(DateTime, default=datetime.utcnow)


class UserProfile(Base):
    __tablename__ = "user_profile"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, default="Cinephile")
    display_name = Column(String, nullable=True)
    bio = Column(String, default="")
    pfp_path = Column(String, nullable=True)  # path to profile picture
    favorite_movie_1 = Column(Integer, nullable=True)  # tmdb_id
    favorite_movie_2 = Column(Integer, nullable=True)
    favorite_movie_3 = Column(Integer, nullable=True)
    favorite_movie_4 = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LoggedMovie(Base):
    __tablename__ = "logged_movies"
    
    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True)
    title = Column(String)
    overview = Column(Text)
    genres = Column(String)  # comma-separated
    poster_path = Column(String, nullable=True)
    release_year = Column(Integer, nullable=True)
    rating = Column(Float)  # 1.0 to 5.0 scale
    embedding = Column(Text)  # JSON-serialized
    logged_at = Column(DateTime, default=datetime.utcnow)


class WatchedMovie(Base):
    __tablename__ = "watched_movies"
    __table_args__ = (UniqueConstraint("title", "release_year", name="uq_watched_title_year"),)

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, nullable=True, index=True)
    title = Column(String, nullable=False)
    release_year = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    letterboxd_uri = Column(String, nullable=True)
    watched_at = Column(DateTime, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)


class Film(Base):
    """Stable film reference table for future collaborative filtering."""
    __tablename__ = "films"

    film_id = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False, index=True)
    year = Column(Integer, nullable=True)
    letterboxd_url = Column(Text, nullable=True)


class User(Base):
    """Multi-user account table for future collaborative filtering."""
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Rating(Base):
    """Collaborative filtering ratings matrix."""
    __tablename__ = "ratings"
    __table_args__ = (CheckConstraint("rating >= 0.5 AND rating <= 5.0", name="ck_ratings_rating_range"),)

    user_id = Column(Integer, ForeignKey("users.user_id"), primary_key=True, index=True)
    film_id = Column(Integer, ForeignKey("films.film_id"), primary_key=True, index=True)
    rating = Column(Float, nullable=False)
    rated_at = Column(DateTime, default=datetime.utcnow)


class UserVector(Base):
    """Cached numpy user vectors for future collaborative filtering."""
    __tablename__ = "user_vectors"

    user_id = Column(Integer, ForeignKey("users.user_id"), primary_key=True)
    vector_blob = Column(LargeBinary, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    # Ensure `display_name` column exists on user_profile for older DBs
    try:
        with engine.connect() as conn:
            res = conn.execute("PRAGMA table_info('user_profile')").fetchall()
            cols = [row[1] for row in res]
            if 'display_name' not in cols:
                conn.execute("ALTER TABLE user_profile ADD COLUMN display_name TEXT")
            if 'password_hash' not in [row[1] for row in conn.execute("PRAGMA table_info('users')").fetchall()]:
                conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    except Exception:
        # Non-fatal: older SQLite versions or missing table will be handled elsewhere
        pass
