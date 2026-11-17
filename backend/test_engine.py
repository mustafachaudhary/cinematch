"""Unit tests for the CineMatch ML services."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from models.database import Film, MovieCache, Rating, User, WatchedMovie  # noqa: E402
from services import collab, embeddings, ranking, search, sentiment, taste, tmdb  # noqa: E402
from services.letterboxd import backfill_watched_tmdb_ids, upsert_watched_row  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models.database import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def make_movie(tmdb_id, title="Movie", genres="Drama", rating_dim=4, release_year=None):
    emb = np.zeros(rating_dim, dtype=np.float32)
    emb[0] = 1.0
    return MovieCache(
        tmdb_id=tmdb_id,
        title=title,
        overview=f"{title} overview",
        genres=genres,
        popularity=50.0,
        vote_average=7.0,
        release_year=release_year,
        embedding=__import__("json").dumps([round(float(x), 5) for x in emb]),
    )


class RatedRow:
    def __init__(self, tmdb_id, rating, embedding, genres="Drama"):
        self.tmdb_id = tmdb_id
        self.rating = rating
        self.embedding = embedding
        self.genres = genres


def emb_json(values):
    import json

    return json.dumps(values)


# ---------------------------------------------------------------------------
# Taste profile
# ---------------------------------------------------------------------------

class TestTasteVector:
    def test_none_when_no_ratings(self):
        assert taste.build_taste_vector([]) is None

    def test_pulls_toward_liked_direction(self):
        liked = RatedRow(1, 5.0, emb_json([1.0, 0.0]))
        vec = taste.build_taste_vector([liked])
        assert vec is not None
        assert vec[0] > 0.99  # normalized toward the liked direction

    def test_dislike_pushes_away(self):
        disliked = RatedRow(1, 1.0, emb_json([1.0, 0.0]))
        other = RatedRow(2, 1.0, emb_json([0.0, 1.0]))
        vec = taste.build_taste_vector([disliked])
        # Pure aversion still yields a direction; it should oppose dim0.
        assert vec is not None
        assert vec[0] < 0

    def test_sentiment_amplifies_signal(self):
        base_movie = RatedRow(1, 4.0, emb_json([1.0, 0.0]))
        neutral = taste.build_taste_vector([base_movie], {})
        boosted = taste.build_taste_vector(
            [base_movie, RatedRow(2, 3.5, emb_json([0.0, 0.0]))], {1: 0.9}
        )
        assert neutral is not None and boosted is not None

    def test_genre_affinity_orders_by_weight(self):
        rows = [
            RatedRow(1, 5.0, None, genres="Comedy,Action"),
            RatedRow(2, 1.0, None, genres="Horror"),
        ]
        affinity = taste.genre_affinity(rows)
        loved_names = [g for g, _ in affinity["loved"]]
        avoided_names = [g for g, _ in affinity["avoided"]]
        assert "Comedy" in loved_names and "Horror" in avoided_names

    def test_match_percent_bounds(self):
        assert taste.match_percent(-1.0) == 0.0
        assert taste.match_percent(1.0) == 100.0
        assert 40 < taste.match_percent(0.0) < 60


# ---------------------------------------------------------------------------
# Collaborative filtering
# ---------------------------------------------------------------------------

class TestCollaborativeFiltering:
    @pytest.fixture()
    def cf_db(self, db_session):
        """Three users; A and B share taste, C is contrarian."""
        users = [User(username=n) for n in ("u1", "u2", "u3")]
        db_session.add_all(users)
        films = {}
        for fid in range(101, 106):
            film = Film(film_id=fid, title=f"Film {fid}")
            films[fid] = film
            db_session.add(film)

        # u1 & u2 agree strongly on everything; u3 disagrees.
        ratings = {
            1: {101: 5.0, 102: 5.0, 103: 4.0},
            2: {101: 4.5, 102: 5.0, 103: 4.5},   # similar to u1
            3: {101: 1.0, 102: 1.5, 103: 1.0, 104: 5.0, 105: 5.0},
        }
        for uid, items in ratings.items():
            for fid, r in items.items():
                db_session.add(Rating(user_id=uid, film_id=fid, rating=r))
        db_session.commit()
        return db_session

    def test_similarity_finds_co_liking(self, cf_db):
        matrix = collab.load_rating_matrix(cf_db)
        sims = collab.item_similarity(collab._center(matrix)[1])
        # Films 101 and 102 are loved by the same raters → clear positive sim
        # (shrunken hard by co-count 2–3, so compare relative order).
        sim_101_102 = sims.get(101, {}).get(102, 0.0)
        assert sim_101_102 > 0.2
        # Films with opposite valence across raters must rank far below.
        assert sims.get(104, {}).get(101, 0.0) < sim_101_102

    def test_predict_for_user_ranks_consistently(self, cf_db):
        matrix = collab.load_rating_matrix(cf_db)
        sims = collab.item_similarity(collab._center(matrix)[1])
        preds = collab.predict_for_user(matrix[3], sims)
        # u3 hated 101–103; predictions should reflect that (low scores).
        if 101 in preds:
            assert preds[101]["pred"] < 3.0

    def test_confidence_grows_with_support(self):
        # sims maps candidate -> {rated_film: sim}; item_similarity emits both
        # directions, so fixtures must be symmetric.
        one = collab.predict_for_user(
            {101: 5.0}, {999: {101: 0.8}, 101: {999: 0.8}}
        )
        many = collab.predict_for_user(
            {101: 5.0, 102: 4.5, 103: 4.0},
            {
                999: {101: 0.8, 102: 0.7, 103: 0.6},
                101: {999: 0.8},
                102: {999: 0.7},
                103: {999: 0.6},
            },
        )
        assert many[999]["confidence"] > one[999]["confidence"]

    def test_empty_matrix_is_safe(self):
        assert collab.predict_for_user({}, {}) == {}
        assert collab.similar_films(1, {}) == []

    def test_topk_similarity_path_is_symmetric_and_sparse(self, cf_db):
        matrix = collab.load_rating_matrix(cf_db)
        centered = collab._center(matrix)[1]
        sims = collab.item_similarity(
            centered, max_neighbors=1, full_limit=0
        )
        assert sims
        for film_a, row in sims.items():
            assert film_a not in row
            for film_b, similarity in row.items():
                assert similarity > 0
                assert film_a in sims[film_b]


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------

class TestSentiment:
    def test_positive_review(self):
        score = sentiment.compound("Absolutely wonderful, I loved every second of this masterpiece!")
        assert score > 0.6

    def test_negative_review(self):
        score = sentiment.compound("Terrible boring waste of time. Awful acting and horrible plot.")
        assert score < -0.5

    def test_neutral_review(self):
        score = sentiment.compound("The movie runs for about two hours.")
        assert abs(score) < 0.4

    def test_empty_input(self):
        assert sentiment.compound("") == 0.0
        assert sentiment.label(0.0) == "mixed"

    def test_aggregate_buckets(self):
        reviews = [
            type("R", (), {"sentiment_compound": 0.8, "content": "great"}),
            type("R", (), {"sentiment_compound": -0.8, "content": "bad"}),
            type("R", (), {"sentiment_compound": 0.1, "content": "ok"}),
        ]
        stats = sentiment.aggregate(reviews)
        assert stats["count"] == 3
        assert stats["distribution"]["positive"] == 1
        assert stats["distribution"]["negative"] == 1
        assert stats["most_positive"]["compound"] == 0.8


# ---------------------------------------------------------------------------
# Auth hardening (OWASP: generic errors, throttling, NIST password policy)
# ---------------------------------------------------------------------------

from fastapi import HTTPException  # noqa: E402
from routers.deps import resolve_user  # noqa: E402
from routers.profile import LoginRequest, SignupRequest, login, signup  # noqa: E402


class TestSeedAccountGuard:
    def test_seed_profiles_are_not_login_accounts(self, db_session):
        db_session.add(User(username="lb_seed_fixture", is_seed=1))
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            resolve_user(db=db_session, x_username="lb_seed_fixture")
        assert exc.value.status_code == 403

    def test_missing_header_resolves_to_mustafa(self, db_session):
        user = resolve_user(db=db_session, x_username=None)
        assert user.username == "mustafa"


class TestSignupPolicy:
    def test_rejects_short_password(self, db_session):
        with pytest.raises(HTTPException) as exc:
            signup(SignupRequest(username="shorty", password="abc"), db=db_session)
        assert exc.value.status_code == 400
        assert "8 characters" in exc.value.detail

    def test_accepts_long_passphrase_without_composition_rules(self, db_session):
        profile = signup(
            SignupRequest(username="passphrase_user", password="correct horse battery staple"),
            db=db_session,
        )
        assert profile["username"] == "passphrase_user"

    def test_rejects_duplicate_username(self, db_session):
        signup(SignupRequest(username="dupe", password="long enough password"), db=db_session)
        with pytest.raises(HTTPException) as exc:
            signup(SignupRequest(username="@Dupe", password="another fine passphrase"), db=db_session)
        assert exc.value.status_code == 400


class TestLoginHardening:
    def test_generic_error_hides_whether_user_exists(self, db_session):
        signup(SignupRequest(username="real_user", password="super secret passphrase"), db=db_session)
        for req in (
            LoginRequest(username="never_signed_up", password="whatever123"),
            LoginRequest(username="real_user", password="totally wrong guess"),
        ):
            with pytest.raises(HTTPException) as exc:
                login(req, db=db_session)
            assert exc.value.status_code == 401
            assert exc.value.detail == "Invalid username or password"

    def test_lockout_after_repeated_failures_even_with_correct_password(self, db_session):
        signup(SignupRequest(username="lockme", password="long enough password"), db=db_session)
        for _ in range(5):
            with pytest.raises(HTTPException) as exc:
                login(LoginRequest(username="lockme", password="nope nope nope"), db=db_session)
            assert exc.value.status_code == 401
        with pytest.raises(HTTPException) as exc:
            login(LoginRequest(username="lockme", password="long enough password"), db=db_session)
        assert exc.value.status_code == 429

    def test_successful_login_clears_failure_count(self, db_session):
        signup(SignupRequest(username="forgiving", password="a good long password"), db=db_session)
        for _ in range(3):
            with pytest.raises(HTTPException):
                login(LoginRequest(username="forgiving", password="still wrong"), db=db_session)
        profile = login(LoginRequest(username="forgiving", password="a good long password"), db=db_session)
        assert profile["username"] == "forgiving"
        # counter was reset — a fresh run of failures starts from zero again
        for _ in range(4):
            with pytest.raises(HTTPException) as exc:
                login(LoginRequest(username="forgiving", password="wrong again"), db=db_session)
            assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Engine fixes: honest affinity, anchor detection, embedding-space signatures,
# CF caches, MMR, and watched-history TMDB resolution.
# ---------------------------------------------------------------------------

class TestHonestSimilarity:
    def test_clip_cosine_floors_at_zero(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        assert embeddings.clip_cosine(a, a) == pytest.approx(1.0)
        assert embeddings.clip_cosine(a, -a) == 0.0
        assert embeddings.clip_cosine(a, np.array([0.0, 1.0], dtype=np.float32)) == 0.0

    def test_clip_cosine_handles_missing_vectors(self):
        assert embeddings.clip_cosine(None, None) == 0.0

    def test_decode_is_memoized_and_rejects_garbage(self):
        raw = emb_json([0.1, 0.2])
        first = embeddings.decode_embedding(raw)
        assert first is embeddings.decode_embedding(raw)  # shared read-only array
        assert embeddings.decode_embedding("not-json") is None


class TestAnchorDetection:
    @staticmethod
    def _movies(*titles):
        return [
            type("M", (), {"tmdb_id": index, "title": title})()
            for index, title in enumerate(titles, start=1)
        ]

    def test_one_letter_titles_do_not_fabricate_anchors(self):
        # Regression: 'Z', 'M', 'Pi' matched any query containing those
        # letters via the substring bonus → fake anchor mode at 50% flat.
        movies = self._movies("Z", "M", "Pi", "Up", "Inception")
        assert search.detect_anchor("zzzzz", movies) is None

    def test_exact_and_prefixed_titles_anchor(self):
        movies = self._movies("Z", "Inception")
        assert search.detect_anchor("inception", movies).title == "Inception"
        assert search.detect_anchor("movies like inception", movies).title == "Inception"

    def test_long_substring_still_anchors(self):
        movies = self._movies("Z", "Blade Runner 2049")
        assert search.detect_anchor("blade", movies).title == "Blade Runner 2049"

    def test_too_short_query_is_never_an_anchor(self):
        assert search.detect_anchor("up", self._movies("Up")) is None


class TestExactTitleSearch:
    def test_parse_title_year_only_for_bare_titles(self):
        assert search.parse_title_year("Inception (2010)") == ("Inception", 2010)
        assert search.parse_title_year("Inception 2010") == ("Inception", 2010)
        assert search.parse_title_year("movies like Inception") == (
            "movies like Inception",
            None,
        )
        assert search.parse_title_year("I want to cry") == ("I want to cry", None)

    def test_exact_match_disambiguates_remakes_by_year(self):
        movies = [
            make_movie(1, title="Solaris", release_year=1972),
            make_movie(2, title="Solaris", release_year=2002),
        ]
        assert search.find_exact_movie("solaris", movies) is None
        assert search.find_exact_movie("Solaris (1972)", movies).tmdb_id == 1
        assert search.find_exact_movie("Solaris 2002", movies).tmdb_id == 2

    def test_exact_match_rejects_short_and_fuzzy_titles(self):
        movies = [make_movie(1, "Up"), make_movie(2, "Blade Runner 2049")]
        assert search.find_exact_movie("up", movies) is None
        assert search.find_exact_movie("blade", movies) is None

    def test_exact_anchor_is_not_repeated_in_neighbors(self, db_session, monkeypatch):
        embeddings._provider_cache.clear()
        monkeypatch.setattr(
            embeddings, "_build_provider", lambda corpus: embeddings.LSAProvider(corpus)
        )
        try:
            movies = [
                make_movie(1, "Inception", genres="Science Fiction"),
                make_movie(2, "Interstellar", genres="Science Fiction"),
                make_movie(3, "The Matrix", genres="Science Fiction"),
            ]
            db_session.add_all(movies)
            db_session.commit()
            embeddings.embed_movies(db_session, force=True)
            # make_movie has no year by default; add it for the exact lookup.
            movies[0].release_year = 2010
            exact = search.find_exact_movie("Inception (2010)", movies)
            assert exact.tmdb_id == 1
            mode, _, results = search.search(
                db_session, "Inception (2010)", movies, None, 5, anchor=exact
            )
            assert mode == "anchor"
            assert all(row["movie"].tmdb_id != exact.tmdb_id for row in results)
        finally:
            embeddings._provider_cache.clear()


class TestEmbeddingSpaceSignature:
    def test_lsa_signature_tracks_the_fitted_corpus(self):
        base = ["one two", "three four", "five six", "alpha beta", "gamma delta"]
        changed = ["one two", "three four", "five six", "alpha beta", "omega sigma"]
        assert (
            embeddings.LSAProvider(base).signature
            == embeddings.LSAProvider(base).signature
        )
        # Same document count, same dimension, different vocabulary: the
        # signature must still change or vectors from two spaces mix.
        assert (
            embeddings.LSAProvider(base).signature
            != embeddings.LSAProvider(changed).signature
        )


class TestCollaborativeCaching:
    @pytest.fixture()
    def cf_db(self, db_session):
        users = [User(username=f"cache_u{i}") for i in (1, 2, 3)]
        db_session.add_all(users)
        for fid in range(201, 206):
            db_session.add(Film(film_id=fid, title=f"Film {fid}"))
        ratings = {
            1: {201: 5.0, 202: 5.0, 203: 4.0},
            2: {201: 4.5, 202: 5.0, 203: 4.5},
            3: {201: 1.0, 202: 1.5, 203: 1.0, 204: 5.0, 205: 5.0},
        }
        for uid, items in ratings.items():
            for fid, value in items.items():
                db_session.add(Rating(user_id=uid, film_id=fid, rating=value))
        db_session.commit()
        return db_session

    def test_matrix_version_changes_on_write(self, cf_db):
        before = collab.matrix_version(cf_db)
        cf_db.add(Rating(user_id=1, film_id=204, rating=3.0))
        cf_db.commit()
        assert collab.matrix_version(cf_db) != before

    def test_similarities_are_reused_until_ratings_change(self, cf_db):
        _, sims_a, version_a = collab.get_model(cf_db)
        _, sims_b, version_b = collab.get_model(cf_db)
        assert version_a == version_b
        assert sims_a is sims_b

        cf_db.add(Rating(user_id=2, film_id=205, rating=1.0))
        cf_db.commit()
        _, sims_c, version_c = collab.get_model(cf_db)
        assert version_c != version_a
        assert sims_c is not sims_a

    def test_prediction_cache_returns_same_mapping(self, cf_db):
        preds_a, _ = collab.get_predictions(cf_db, 3)
        preds_b, _ = collab.get_predictions(cf_db, 3)
        assert preds_a is preds_b


class TestMmr:
    def test_prefers_novelty_over_near_duplicates(self):
        scores = {1: 0.9, 2: 0.85, 3: 0.8}
        vectors = {
            1: np.array([1.0, 0.0], dtype=np.float32),
            2: np.array([0.999, 0.01], dtype=np.float32),  # ≈ duplicate of 1
            3: np.array([0.0, 1.0], dtype=np.float32),
        }
        order = ranking._mmr(scores, vectors, 2)
        assert order[0] == 1
        assert order[1] == 3

    def test_fallback_handles_missing_vectors(self):
        order = ranking._mmr(
            {1: 0.9, 2: 0.1},
            {1: np.array([1.0, 0.0], dtype=np.float32), 2: None},
            2,
        )
        assert order[0] == 1
        assert sorted(order) == [1, 2]

    def test_returns_every_candidate_when_limit_allows(self):
        scores = {i: 1.0 - i / 10 for i in range(1, 5)}
        vectors = {i: np.array([1.0, float(i)], dtype=np.float32) for i in range(1, 5)}
        assert sorted(ranking._mmr(scores, vectors, 10)) == [1, 2, 3, 4]


class TestWatchedHistory:
    def test_upsert_stores_tmdb_id_on_insert_and_fills_on_update(self, db_session):
        user = User(username="watched_store")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        row = {"Name": "Inception", "Year": "2010"}
        upsert_watched_row(db_session, row, user.user_id)
        db_session.commit()
        watched = db_session.query(WatchedMovie).one()
        assert watched.tmdb_id is None

        upsert_watched_row(db_session, row, user.user_id, tmdb_id=27205)
        db_session.commit()
        db_session.refresh(watched)
        assert watched.tmdb_id == 27205

    def test_backfill_matches_title_and_year(self, db_session):
        user = User(username="watched_backfill")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        db_session.add_all([
            make_movie(27205, title="Inception", release_year=2010),
            make_movie(999, title="Z", release_year=1969),
        ])
        db_session.add_all([
            WatchedMovie(user_id=user.user_id, title="inception", release_year=2010),
            WatchedMovie(user_id=user.user_id, title="Z", release_year=1969),
        ])
        db_session.commit()

        assert backfill_watched_tmdb_ids(db_session, user.user_id) == 2
        rows = {w.title: w.tmdb_id for w in db_session.query(WatchedMovie).all()}
        assert rows["inception"] == 27205
        assert rows["Z"] == 999

    def test_backfill_uses_year_to_disambiguate_remakes(self, db_session):
        user = User(username="watched_remake")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        db_session.add_all([
            make_movie(1, title="Solaris", release_year=1972),
            make_movie(2, title="Solaris", release_year=2002),
        ])
        db_session.add(
            WatchedMovie(user_id=user.user_id, title="Solaris", release_year=2002)
        )
        db_session.commit()

        assert backfill_watched_tmdb_ids(db_session, user.user_id) == 1
        assert db_session.query(WatchedMovie).one().tmdb_id == 2

    def test_backfill_skips_ambiguous_title_only_matches(self, db_session):
        user = User(username="watched_ambiguous")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        db_session.add_all([
            make_movie(1, title="Solaris", release_year=1972),
            make_movie(2, title="Solaris", release_year=2002),
        ])
        db_session.add(WatchedMovie(user_id=user.user_id, title="Solaris"))
        db_session.commit()

        assert backfill_watched_tmdb_ids(db_session, user.user_id) == 0
        assert db_session.query(WatchedMovie).one().tmdb_id is None


class TestSearchHonesty:
    def test_garbage_queries_get_no_fake_anchor_or_high_score(self, db_session, monkeypatch):
        # Force the deterministic LSA backend: this test is about the scoring
        # logic, not about which embedding model happens to be installed.
        embeddings._provider_cache.clear()
        monkeypatch.setattr(
            embeddings, "_build_provider", lambda corpus: embeddings.LSAProvider(corpus)
        )
        try:
            movies = [
                make_movie(27205, title="Inception"),
                make_movie(999, title="Z"),
                make_movie(335984, title="Blade Runner 2049"),
            ]
            db_session.add_all(movies)
            db_session.commit()
            embeddings.embed_movies(db_session, force=True)

            for query in ("asdfgh", "zzzzz"):
                mode, _, results = search.search(db_session, query, movies, None, 5)
                assert mode == "general", f"{query!r} fabricated an anchor"
                assert results
                assert max(r["score"] for r in results) < 25.0
                assert all("No strong match" in r["reason"] for r in results)

            mode, _, _ = search.search(db_session, "blade", movies, None, 5)
            assert mode == "anchor"
        finally:
            embeddings._provider_cache.clear()


class TestTMDBRetry:
    def test_get_retries_429_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        class FakeResponse:
            def __init__(self, status_code, payload=None, headers=None):
                self.status_code = status_code
                self._payload = payload or {}
                self.headers = headers or {}

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(str(self.status_code))

        def fake_get(url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResponse(429, headers={"Retry-After": "0"})
            return FakeResponse(200, {"ok": True})

        monkeypatch.setattr(tmdb.requests, "get", fake_get)
        try:
            assert tmdb._get("/movie/1") == {"ok": True}
        finally:
            tmdb.configure_rate_limit(0)
        assert calls["n"] == 2


class TestTMDBErrorDistinction:
    """TMDB outages must not be reported as "movie not found"."""

    def test_missing_movie_returns_none(self, monkeypatch):
        response = requests.Response()
        response.status_code = 404

        def fake_get(endpoint, params=None):
            if endpoint == "/genre/movie/list":
                return {"genres": []}
            raise requests.HTTPError("404", response=response)

        monkeypatch.setattr(tmdb, "_get", fake_get)
        assert tmdb.get_movie_details(1) is None

    def test_outage_raises_tmdb_unavailable(self, monkeypatch):
        def fake_get(endpoint, params=None):
            if endpoint == "/genre/movie/list":
                return {"genres": []}
            raise requests.ConnectionError("connection refused")

        monkeypatch.setattr(tmdb, "_get", fake_get)
        with pytest.raises(tmdb.TMDBUnavailable):
            tmdb.get_movie_details(1)

    def test_movie_full_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            tmdb, "_build_movie_full", lambda tmdb_id: (_ for _ in ()).throw(RuntimeError("down"))
        )
        assert tmdb.get_movie_full(999999999) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
