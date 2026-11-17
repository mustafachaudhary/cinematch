"""Unit tests for the seed resolution/import pipeline.

These are deliberately network-free: they exercise the strict matcher, the
IMDb-link parser, the per-profile row builder, and the additive schema.
"""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from letterboxd_common import extract_imdb_id  # noqa: E402
from resolve_seed_ratings import choose_match, film_key, normalize_title  # noqa: E402
from import_seed_profiles import build_profile_rows  # noqa: E402
from apply_themes import canonical_themes  # noqa: E402
from models import database  # noqa: E402
from services import tmdb  # noqa: E402


def _movie(tmdb_id, title, year, popularity=1.0):
    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "release_year": year,
        "popularity": popularity,
        "vote_average": 7.0,
        "genres": ["Drama"],
        "overview": "",
        "poster_path": None,
    }


def test_normalize_title_handles_articles_and_punctuation():
    assert normalize_title("The Lord of the Rings: The Fellowship") == (
        "lord of the rings the fellowship"
    )
    assert normalize_title("Amélie") == "amelie"


def test_film_key_keeps_unknown_year_distinct():
    assert film_key("some-film", 2001) == "some-film|2001"
    assert film_key("some-film", None) == "some-film|"


def test_exact_title_year_match_wins():
    candidates = [
        _movie(1, "The Odyssey", 2026, 10.0),
        _movie(2, "The Odyssey", 1997, 1.0),
    ]
    movie, method = choose_match(candidates, "The Odyssey", 2026)
    assert movie["tmdb_id"] == 1
    assert method == "title_year"


def test_release_year_off_by_one_is_accepted_only_when_unique():
    movie, method = choose_match([_movie(3, "Patch's Adventure", 2003)], "Patch's Adventure", 2002)
    assert movie["tmdb_id"] == 3
    assert method == "title_year_pm1"


def test_same_title_and_year_needs_popularity_margin():
    candidates = [
        _movie(1, "The Odyssey", 2026, 10.0),
        _movie(2, "The Odyssey", 2026, 9.0),
    ]
    movie, reason = choose_match(candidates, "The Odyssey", 2026)
    assert movie is None
    assert reason == "ambiguous_year"

    candidates[1]["popularity"] = 2.0
    movie, method = choose_match(candidates, "The Odyssey", 2026)
    assert movie["tmdb_id"] == 1
    assert method == "title_year_margin"


def test_no_exact_title_is_never_guessed():
    movie, reason = choose_match([_movie(1, "Odysseys", 2026)], "The Odyssey", 2026)
    assert movie is None
    assert reason == "no_exact_title"


def test_article_variant_is_only_accepted_in_the_same_year():
    same_year = [_movie(1, "The School of Rock", 2003)]
    movie, method = choose_match(same_year, "School of Rock", 2003)
    assert movie["tmdb_id"] == 1
    assert method == "title_year_article"

    off_by_one = [_movie(2, "The ATM", 2016)]
    movie, reason = choose_match(off_by_one, "ATM", 2017)
    assert movie is None
    assert reason == "no_exact_title"


def test_extract_imdb_id_from_link_and_data_attribute():
    assert (
        extract_imdb_id('<a href="https://www.imdb.com/title/tt0133093/">IMDb</a>')
        == "tt0133093"
    )
    assert extract_imdb_id('<div data-imdb-id="tt0111161"></div>') == "tt0111161"
    assert extract_imdb_id("<html>no ids here</html>") is None


def test_find_by_imdb_returns_normalized_movie(monkeypatch):
    def fake_get(endpoint, params=None):
        if endpoint == "/genre/movie/list":
            return {"genres": [{"id": 18, "name": "Drama"}]}
        assert endpoint == "/find/tt0133093"
        return {
            "movie_results": [
                {
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-31",
                    "genre_ids": [18],
                    "popularity": 50.0,
                    "vote_average": 8.2,
                    "overview": "A hacker learns the truth.",
                    "poster_path": "/matrix.jpg",
                }
            ]
        }

    monkeypatch.setattr(tmdb, "_get", fake_get)
    tmdb.get_genre_map.cache_clear()
    movie = tmdb.find_by_imdb("tt0133093")
    assert movie["tmdb_id"] == 603
    assert movie["title"] == "The Matrix"
    assert movie["genres"] == ["Drama"]
    tmdb.get_genre_map.cache_clear()


def test_get_movie_metadata_normalizes_credits_keywords_and_edges(monkeypatch):
    def fake_get(endpoint, params=None):
        assert endpoint == "/movie/603"
        return {
            "id": 603,
            "title": "The Matrix",
            "overview": "A hacker learns the truth.",
            "release_date": "1999-03-31",
            "runtime": 136,
            "original_language": "en",
            "vote_average": 8.2,
            "vote_count": 25000,
            "popularity": 50.0,
            "poster_path": "/matrix.jpg",
            "genres": [{"id": 18, "name": "Drama"}],
            "belongs_to_collection": {"id": 2344},
            "external_ids": {"imdb_id": "tt0133093"},
            "keywords": {"keywords": [{"name": "cyberpunk"}]},
            "credits": {
                "crew": [
                    {"id": 1, "name": "Lana Wachowski", "job": "Director"},
                    {"id": 2, "name": "Lilly Wachowski", "job": "Writer"},
                ],
                "cast": [{"id": 3, "name": "Keanu Reeves", "order": 0}],
            },
            "similar": {"results": [{"id": 604}]},
            "recommendations": {"results": [{"id": 605}]},
        }

    monkeypatch.setattr(tmdb, "_get", fake_get)
    movie = tmdb.get_movie_metadata(603)
    assert movie["runtime"] == 136
    assert movie["collection_id"] == 2344
    assert movie["imdb_id"] == "tt0133093"
    assert movie["keywords"] == ["cyberpunk"]
    assert {(p["person_id"], p["role"]) for p in movie["people"]} == {
        (1, "director"),
        (2, "writer"),
        (3, "cast"),
    }
    assert {(r["related_tmdb_id"], r["source"]) for r in movie["related"]} == {
        (604, "similar"),
        (605, "recommendations"),
    }


def test_theme_canonicalization_drops_genres_and_weights_keywords_lower():
    themes = canonical_themes(
        ["Drama", "Mind-bending", "mind-bending"],
        source="tmdb_keyword",
        weight=0.6,
        genres={"drama"},
    )
    assert themes == [
        {
            "id": "kw:mind-bending",
            "label": "Mind-bending",
            "weight": 0.6,
            "source": "tmdb_keyword",
        }
    ]


def test_build_profile_rows_keeps_likes_separate_and_dedupes():
    profile = {
        "pseudonym": "lb_test",
        "state": {"source": "unit", "films": {"flags": []}},
        "rows": [
            {"slug": "x", "year": 2000, "title": "X", "rating": 4.5},
            {"slug": "y", "year": 2001, "title": "Y", "rating": None},
            {"slug": "missing", "year": 2002, "title": "Missing", "rating": 3.0},
        ],
        "liked": [
            {"slug": "x", "year": 2000, "title": "X"},
            {"slug": "missing-like", "year": 2002, "title": "Missing Like"},
        ],
    }
    resolved = {
        "x|2000": (1, _movie(1, "X", 2000)),
        "y|2001": (2, _movie(2, "Y", 2001)),
    }
    ratings, watched, liked, unresolved_ratings, unresolved_likes = build_profile_rows(
        profile, resolved
    )
    assert ratings == {1: {"rating": 4.5, "movie": resolved["x|2000"][1]}}
    assert set(watched) == {("Y", 2001)}
    assert set(liked) == {1}
    assert unresolved_ratings == 1
    assert unresolved_likes == 1


def test_new_tables_are_created_idempotently():
    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(engine)
    database.Base.metadata.create_all(engine)
    from sqlalchemy import inspect

    names = set(inspect(engine).get_table_names())
    assert {
        "user_likes",
        "seed_profile",
        "film_people",
        "film_related",
        "user_feedback",
        "feed_impressions",
    } <= names


def test_phase3_movie_columns_are_added_idempotently():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE movie_cache (id INTEGER PRIMARY KEY, tmdb_id INTEGER, title TEXT)"
        )
        database._add_missing_movie_columns(conn)
        database._add_missing_movie_columns(conn)
        columns = set(database._table_columns(conn, "movie_cache"))
    assert set(database._MOVIE_COLUMNS) <= columns
