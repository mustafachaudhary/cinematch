import hashlib
import os
import secrets
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

raw_db_url = os.getenv("DATABASE_URL", "sqlite:///./cinematch.db")
if raw_db_url.startswith("sqlite:///./"):
    rel = raw_db_url.replace("sqlite:///./", "")
    abs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), rel)
    DATABASE_URL = f"sqlite:///{abs_path}"
else:
    DATABASE_URL = raw_db_url

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class MovieCache(Base):
    """Canonical catalog of movies known to the app (with precomputed embeddings)."""

    __tablename__ = "movie_cache"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    overview = Column(Text)
    genres = Column(String)  # comma-separated
    poster_path = Column(String, nullable=True)
    release_year = Column(Integer, nullable=True)
    popularity = Column(Float, default=0.0)
    vote_average = Column(Float, default=0.0)
    runtime = Column(Integer, nullable=True)
    original_language = Column(String, nullable=True)
    vote_count = Column(Integer, nullable=True)
    collection_id = Column(Integer, nullable=True)
    imdb_id = Column(String, nullable=True, index=True)
    letterboxd_slug = Column(String, nullable=True, index=True)
    themes = Column(Text, nullable=True)  # JSON [{id,label,weight,source}]
    keywords = Column(Text, nullable=True)  # JSON [label]
    assets = Column(Text, nullable=True)  # JSON detail payload (backdrop/trailer/etc.)
    text_version = Column(String, nullable=True)
    embedding = Column(Text)  # JSON-serialized unit vector
    cached_at = Column(DateTime, default=datetime.utcnow)


class LetterboxdPopularity(Base):
    """Letterboxd all-time popularity rank for the expanded catalog.

    ``rank`` is the rank in the TV-filtered film list (1 = most popular).  Rows
    are retained for skipped/unresolved entries so the harvest is auditable and
    a later retry does not lose the source position.
    """

    __tablename__ = "letterboxd_popularity"

    rank = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    tmdb_id = Column(Integer, index=True, nullable=True)
    letterboxd_uid = Column(String, nullable=True)
    average_rating = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="resolved")
    source = Column(String, nullable=False, default="letterboxd_popular_csi")
    notes = Column(Text, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)


class FilmNeighbor(Base):
    """Precomputed local recommendation edge for ``more like this``.

    Replaces the runtime full-catalog relationship scan.  Edges are directional
    (anchor -> neighbor) and keep the evidence that produced the score.
    ``source='behavior_v1'`` is the local ratings/likes/watches graph;
    ``source='hybrid_v1'`` is the metadata cold-start fallback.
    """

    __tablename__ = "film_neighbors"
    __table_args__ = (
        UniqueConstraint(
            "tmdb_id", "neighbor_tmdb_id", "source", name="uq_film_neighbor_source"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, index=True, nullable=False)
    neighbor_tmdb_id = Column(Integer, index=True, nullable=False)
    score = Column(Float, nullable=False)
    rank = Column(Integer, nullable=False)
    source = Column(String, nullable=False, default="hybrid_v1")
    evidence = Column(Text, nullable=True)  # JSON signal breakdown
    created_at = Column(DateTime, default=datetime.utcnow)


class ThemeCluster(Base):
    """Co-occurrence cluster of Letterboxd themes/mini-themes.

    A cluster is a derived subniche, not a hand-written genre.  ``themes`` is a
    JSON list of canonical theme ids, and ``label`` is generated from the
    highest-support member themes.
    """

    __tablename__ = "theme_clusters"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    label = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("theme_clusters.id"), nullable=True)
    level = Column(String, nullable=False, default="subniche")
    size = Column(Integer, nullable=False, default=0)
    themes = Column(Text, nullable=True)  # JSON [canonical theme id]
    created_at = Column(DateTime, default=datetime.utcnow)


class FilmSubniche(Base):
    """Weighted film -> derived subniche mapping."""

    __tablename__ = "film_subniches"
    __table_args__ = (
        UniqueConstraint("tmdb_id", "cluster_id", name="uq_film_subniche"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, index=True, nullable=False)
    cluster_id = Column(Integer, ForeignKey("theme_clusters.id"), index=True, nullable=False)
    weight = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=True)
    is_seed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    display_name = Column(String, nullable=True)
    bio = Column(String, default="")
    pfp_path = Column(String, nullable=True)
    favorite_movie_1 = Column(Integer, nullable=True)
    favorite_movie_2 = Column(Integer, nullable=True)
    favorite_movie_3 = Column(Integer, nullable=True)
    favorite_movie_4 = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserLike(Base):
    """A binary like, deliberately separate from the star-rating matrix."""

    __tablename__ = "user_likes"
    __table_args__ = (UniqueConstraint("user_id", "tmdb_id", name="uq_user_like"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, ForeignKey("movie_cache.tmdb_id"), index=True, nullable=False)
    liked_at = Column(DateTime, nullable=True)


class SeedProfile(Base):
    """Pseudonymous provenance for a harvested Letterboxd seed account."""

    __tablename__ = "seed_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.user_id"), unique=True, index=True, nullable=False
    )
    cohort = Column(String, nullable=True)
    handle_hash = Column(String, unique=True, index=True, nullable=False)
    sampled = Column(Integer, nullable=False, default=0)
    n_items = Column(Integer, nullable=False, default=0)
    imported_at = Column(DateTime, default=datetime.utcnow)


class FilmPerson(Base):
    """Director/writer/cast credit used for person-affinity ranking."""

    __tablename__ = "film_people"
    __table_args__ = (
        UniqueConstraint("tmdb_id", "person_id", "role", name="uq_film_person_role"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, index=True, nullable=False)
    person_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    ordering = Column(Integer, nullable=True)
    profile_path = Column(String, nullable=True)
    source = Column(String, nullable=False, default="tmdb")


class FilmRelated(Base):
    """Franchise/collection/similar edges used for related-film boosts."""

    __tablename__ = "film_related"
    __table_args__ = (
        UniqueConstraint(
            "tmdb_id", "related_tmdb_id", "source", name="uq_film_related_source"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, index=True, nullable=False)
    related_tmdb_id = Column(Integer, nullable=False)
    source = Column(String, nullable=False)
    rank = Column(Integer, nullable=True)


class UserFeedback(Base):
    """Explicit hide / not-interested feedback with a bounded scoring effect."""

    __tablename__ = "user_feedback"
    __table_args__ = (
        UniqueConstraint("user_id", "tmdb_id", "kind", name="uq_user_feedback_kind"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, index=True, nullable=False)
    kind = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class WatchlistItem(Base):
    """A film a user has set aside to watch later."""

    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("user_id", "tmdb_id", name="uq_watchlist_user_movie"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, ForeignKey("movie_cache.tmdb_id"), index=True, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)


class FeedImpression(Base):
    """Render-confirmed feed impression, deduped per user/film/time window."""

    __tablename__ = "feed_impressions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, index=True, nullable=False)
    session_id = Column(String, nullable=True)
    shown_at = Column(DateTime, default=datetime.utcnow)


class LoggedMovie(Base):
    """A user's rating for a movie. Content lives in MovieCache; this is per-user."""

    __tablename__ = "logged_movies"
    __table_args__ = (
        UniqueConstraint("user_id", "tmdb_id", name="uq_logged_user_movie"),
        CheckConstraint("rating >= 0.5 AND rating <= 5.0", name="ck_logged_rating_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, ForeignKey("movie_cache.tmdb_id"), index=True, nullable=False)
    rating = Column(Float, nullable=False)  # 0.5 – 5.0 stars
    logged_at = Column(DateTime, default=datetime.utcnow)


class Review(Base):
    """Free-text review; sentiment is analyzed on write and feeds taste signals."""

    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, index=True, nullable=False)
    content = Column(Text, nullable=False)
    sentiment_compound = Column(Float, nullable=False, default=0.0)
    sentiment_pos = Column(Float, nullable=False, default=0.0)
    sentiment_neu = Column(Float, nullable=False, default=0.0)
    sentiment_neg = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "tmdb_id", name="uq_review_user_movie"),)


class WatchedMovie(Base):
    """Letterboxd watch history (title-based; may have no TMDB match)."""

    __tablename__ = "watched_movies"
    __table_args__ = (
        UniqueConstraint("user_id", "title", "release_year", name="uq_watched_user_title_year"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), index=True, nullable=False)
    tmdb_id = Column(Integer, nullable=True, index=True)
    title = Column(String, nullable=False)
    release_year = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    letterboxd_uri = Column(String, nullable=True)
    watched_at = Column(DateTime, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)


class Film(Base):
    """Stable film dimension for the collaborative filtering matrix."""

    __tablename__ = "films"

    film_id = Column(Integer, primary_key=True)  # == tmdb_id
    title = Column(Text, nullable=False, index=True)
    year = Column(Integer, nullable=True)


class Rating(Base):
    """Explicit user×film rating matrix used by collaborative filtering."""

    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("rating >= 0.5 AND rating <= 5.0", name="ck_ratings_rating_range"),
    )

    user_id = Column(Integer, ForeignKey("users.user_id"), primary_key=True, index=True)
    film_id = Column(Integer, ForeignKey("films.film_id"), primary_key=True, index=True)
    rating = Column(Float, nullable=False)
    rated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppMeta(Base):
    """Small key/value store (embedding provider signature, schema version, …)."""

    __tablename__ = "app_meta"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Passwords: PBKDF2-SHA256 with per-user salt. Legacy unsalted SHA-256 hashes
# are still verified so existing accounts keep working, and re-hashed on login.
# ---------------------------------------------------------------------------

PBKDF2_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str | None) -> bool:
    stored = stored or ""
    if stored.startswith("pbkdf2$"):
        try:
            _, iterations, salt, digest = stored.split("$")
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt),
                int(iterations),
            ).hex()
            return secrets.compare_digest(candidate, digest)
        except (ValueError, TypeError):
            return False
    # Legacy unsalted sha256 (64 hex chars)
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return secrets.compare_digest(legacy, stored)


def needs_rehash(stored: str | None) -> bool:
    return not (stored or "").startswith("pbkdf2$")


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def _table_columns(conn, table: str) -> list[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info('{table}')").fetchall()
    return [row[1] for row in rows]


def _migrate_logged_movies(conn) -> None:
    """logged_movies v1 was a single-user table that duplicated movie content.

    Rebuild it as a slim per-user rating table joined against movie_cache, and
    carry legacy rows into the CF ratings matrix.
    """
    cols = _table_columns(conn, "logged_movies")
    if not cols or "user_id" in cols:
        return

    conn.exec_driver_sql(
        """
        CREATE TABLE logged_movies_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            tmdb_id INTEGER NOT NULL REFERENCES movie_cache(tmdb_id),
            rating FLOAT NOT NULL CHECK (rating >= 0.5 AND rating <= 5.0),
            logged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_logged_user_movie UNIQUE (user_id, tmdb_id)
        )
        """
    )

    legacy_users = conn.exec_driver_sql(
        "SELECT user_id FROM users ORDER BY user_id LIMIT 1"
    ).fetchone()
    if legacy_users:
        default_uid = legacy_users[0]
    else:
        conn.exec_driver_sql(
            "INSERT INTO users (username, created_at) VALUES ('mustafa', CURRENT_TIMESTAMP)"
        )
        default_uid = conn.exec_driver_sql(
            "SELECT user_id FROM users ORDER BY user_id DESC LIMIT 1"
        ).fetchone()[0]

    # Copy legacy rows; rows whose movie vanished from the cache are dropped.
    rows = conn.exec_driver_sql(
        "SELECT tmdb_id, rating, logged_at FROM logged_movies ORDER BY id"
    ).fetchall()
    carried = 0
    for tmdb_id, rating, logged_at in rows:
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM movie_cache WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        if not exists:
            continue
        rating = max(0.5, min(5.0, float(rating or 3.0)))
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO logged_movies_new (user_id, tmdb_id, rating, logged_at)"
            " VALUES (?, ?, ?, ?)",
            (default_uid, tmdb_id, rating, logged_at),
        )
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO films (film_id, title) SELECT tmdb_id, title"
            " FROM movie_cache WHERE tmdb_id = ?",
            (tmdb_id,),
        )
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO ratings (user_id, film_id, rating, rated_at)"
            " VALUES (?, ?, ?, ?)",
            (default_uid, tmdb_id, rating, logged_at),
        )
        carried += 1

    conn.exec_driver_sql("DROP TABLE logged_movies")
    conn.exec_driver_sql("ALTER TABLE logged_movies_new RENAME TO logged_movies")
    conn.exec_driver_sql(
        "CREATE INDEX ix_logged_movies_user ON logged_movies(user_id)"
    )
    print(f"[migrate] logged_movies rebuilt ({carried} legacy rows carried over)")


def _migrate_watched_movies(conn) -> None:
    """watched_movies v1 was single-user (no user_id). Rebuild it per-user,
    assigning legacy rows to the first account, like logged_movies."""
    cols = _table_columns(conn, "watched_movies")
    if not cols or "user_id" in cols:
        return

    conn.exec_driver_sql(
        """
        CREATE TABLE watched_movies_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            tmdb_id INTEGER,
            title VARCHAR NOT NULL,
            release_year INTEGER,
            rating FLOAT,
            letterboxd_uri VARCHAR,
            watched_at DATETIME,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_watched_user_title_year UNIQUE (user_id, title, release_year)
        )
        """
    )

    owner = conn.exec_driver_sql(
        "SELECT user_id FROM users ORDER BY user_id LIMIT 1"
    ).fetchone()
    if not owner:
        return
    default_uid = owner[0]

    shared = [c for c in ("tmdb_id", "title", "release_year", "rating",
                          "letterboxd_uri", "watched_at", "imported_at") if c in cols]
    select_list = ", ".join(shared) if shared else "NULL"
    placeholders = ", ".join("?" for _ in shared)
    rows = conn.exec_driver_sql(f"SELECT {select_list} FROM watched_movies").fetchall()
    insert_sql = (
        f"INSERT OR IGNORE INTO watched_movies_new (user_id, {', '.join(shared)}) "
        f"VALUES (?, {placeholders})"
    ) if shared else (
        "INSERT OR IGNORE INTO watched_movies_new (user_id) VALUES (?)"
    )
    carried = 0
    for row in rows:
        conn.exec_driver_sql(insert_sql, (default_uid, *row))
        carried += 1

    conn.exec_driver_sql("DROP TABLE watched_movies")
    conn.exec_driver_sql("ALTER TABLE watched_movies_new RENAME TO watched_movies")
    conn.exec_driver_sql("CREATE INDEX ix_watched_movies_user ON watched_movies(user_id)")
    print(f"[migrate] watched_movies rebuilt ({carried} legacy rows carried over)")


def _add_missing_columns(conn) -> None:
    if "display_name" not in _table_columns(conn, "user_profile"):
        conn.exec_driver_sql("ALTER TABLE user_profile ADD COLUMN display_name TEXT")
    if "password_hash" not in _table_columns(conn, "users"):
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "is_seed" not in _table_columns(conn, "users"):
        conn.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN is_seed INTEGER NOT NULL DEFAULT 0"
        )
    if "profile_path" not in _table_columns(conn, "film_people"):
        conn.exec_driver_sql(
            "ALTER TABLE film_people ADD COLUMN profile_path TEXT"
        )
    # Letterboxd seed profiles are recommendation data, never login accounts.
    conn.exec_driver_sql("UPDATE users SET is_seed = 1 WHERE username GLOB 'lb_*'")
    # user_profile.username became unique in v2; SQLite cannot easily alter
    # constraints, uniqueness is enforced at the application layer instead.


# Phase 3 metadata. `create_all` cannot alter an existing table, so every new
# movie_cache column is added explicitly and guarded by PRAGMA table_info.
_MOVIE_COLUMNS = {
    "runtime": "INTEGER",
    "original_language": "TEXT",
    "vote_count": "INTEGER",
    "collection_id": "INTEGER",
    "imdb_id": "TEXT",
    "letterboxd_slug": "TEXT",
    "themes": "TEXT",
    "keywords": "TEXT",
    "assets": "TEXT",
    "text_version": "TEXT",
}


def _add_missing_movie_columns(conn) -> None:
    existing = set(_table_columns(conn, "movie_cache"))
    for name, sql_type in _MOVIE_COLUMNS.items():
        if name not in existing:
            conn.exec_driver_sql(
                f"ALTER TABLE movie_cache ADD COLUMN {name} {sql_type}"
            )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_movie_cache_imdb_id ON movie_cache(imdb_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_movie_cache_letterboxd_slug "
        "ON movie_cache(letterboxd_slug)"
    )


def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        _migrate_logged_movies(conn)
        _migrate_watched_movies(conn)
        _add_missing_columns(conn)
        _add_missing_movie_columns(conn)


def get_meta(db, key: str, default=None):
    row = db.query(AppMeta).filter(AppMeta.key == key).first()
    return row.value if row else default


def set_meta(db, key: str, value: str):
    row = db.query(AppMeta).filter(AppMeta.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppMeta(key=key, value=value))
    db.commit()
