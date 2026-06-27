-- schema.sql
-- Database schema for CineMatch multi-user collaborative filtering support
-- SQLite-compatible syntax

-- Films table (stable reference)
CREATE TABLE films (
    film_id     INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    year        INTEGER,
    letterboxd_url TEXT
);

-- Users table
CREATE TABLE users (
    user_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Ratings table (the CF matrix lives here)
CREATE TABLE ratings (
    user_id     INTEGER REFERENCES users(user_id),
    film_id     INTEGER REFERENCES films(film_id),
    rating      REAL NOT NULL CHECK(rating >= 0.5 AND rating <= 5.0),
    rated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, film_id)
);

-- User preference vectors (cached, rebuilt when ratings change)
CREATE TABLE user_vectors (
    user_id     INTEGER PRIMARY KEY REFERENCES users(user_id),
    vector_blob BLOB NOT NULL,   -- numpy array serialized with np.tobytes()
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX idx_ratings_user ON ratings(user_id);
CREATE INDEX idx_ratings_film ON ratings(film_id);
CREATE INDEX idx_films_title ON films(title);