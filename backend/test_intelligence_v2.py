"""Focused tests for ranking-v2 signals and explicit feedback."""

import json
from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import (
    Base,
    FeedImpression,
    FilmSubniche,
    LoggedMovie,
    MovieCache,
    ThemeCluster,
    User,
    UserFeedback,
    UserLike,
)
from services import features
from services import feedback as feedback_svc
from services import ranking
from services import search as search_svc
from services import taste


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def add_movie(db, tmdb_id, *, genres="Drama", themes=None, collection_id=None, vector=(1.0, 0.0)):
    movie = MovieCache(
        tmdb_id=tmdb_id,
        title=f"Film {tmdb_id}",
        overview="A film about memory and loss.",
        genres=genres,
        popularity=10.0,
        vote_average=7.5,
        vote_count=500,
        collection_id=collection_id,
        themes=json.dumps(themes) if themes else None,
        embedding=json.dumps(list(vector)),
    )
    db.add(movie)
    return movie


def add_user(db, user_id=1, username="tester"):
    db.add(User(user_id=user_id, username=username, password_hash="x", is_seed=0))


def test_likes_are_a_separate_positive_centroid(db):
    add_user(db)
    add_movie(db, 1, vector=(1.0, 0.0), themes=[{"id": "memory", "label": "Memory"}])
    add_movie(db, 2, vector=(0.0, 1.0), themes=[{"id": "grief", "label": "Grief"}])
    db.add(LoggedMovie(user_id=1, tmdb_id=1, rating=2.0))
    db.add(UserLike(user_id=1, tmdb_id=2))
    db.commit()

    signals = features.load_user_signals(db, 1)
    assert signals.has_likes
    assert signals.liked_ids == {2}
    assert signals.liked_vec[1] > 0.99
    assert signals.genre_support["Drama"] == 2


def test_theme_and_collection_match_are_bounded(db):
    add_user(db)
    add_movie(
        db,
        1,
        themes=[{"id": "memory", "label": "Memory", "weight": 1.0}],
        collection_id=9,
        vector=(1.0, 0.0),
    )
    for index in range(2, 8):
        add_movie(
            db,
            index,
            themes=[{"id": "memory", "label": "Memory", "weight": 1.0}],
            collection_id=9 if index == 2 else None,
            vector=(0.0, 1.0),
        )
        db.add(LoggedMovie(user_id=1, tmdb_id=index, rating=5.0))
    db.commit()

    signals = features.load_user_signals(db, 1)
    score, labels = features.theme_match(
        features.parse_themes(db.query(MovieCache).filter_by(tmdb_id=1).one().themes),
        signals,
    )
    assert score is not None and 0.5 < score <= 1.0
    assert labels == ["Memory"]
    collection = features.collection_match(9, signals)
    assert collection is not None and 0.5 < collection <= 1.0
    assert features.collection_match(999, signals) is None


def test_age_multiplier_is_opt_in_and_floored(monkeypatch):
    old = datetime.utcnow() - timedelta(days=365)
    ancient = datetime.utcnow() - timedelta(days=730)
    # Off by default: uniform date columns make decay an exact no-op.
    assert taste.age_multiplier(old) == 1.0

    monkeypatch.setenv("CINEMATCH_DECAY", "1")
    assert taste.age_multiplier(None) == 1.0
    assert taste.age_multiplier(datetime.utcnow()) > 0.999
    assert 0.49 < taste.age_multiplier(old) < 0.51  # one half-life
    assert taste.age_multiplier(ancient) == taste.DECAY_FLOOR  # floored


def test_decay_downweights_old_ratings_in_the_taste_vector(db, monkeypatch):
    add_user(db)
    add_movie(db, 1, vector=(1.0, 0.0))
    db.add(LoggedMovie(user_id=1, tmdb_id=1, rating=5.0))  # recent drama love
    add_movie(db, 2, vector=(0.0, 1.0))
    db.add(
        LoggedMovie(
            user_id=1,
            tmdb_id=2,
            rating=5.0,
            logged_at=datetime.utcnow() - timedelta(days=365 * 4),
        )
    )  # a phase left years ago
    db.commit()

    rated = taste.load_rated_movies(db, 1)
    balanced = taste.build_taste_vector(rated)
    assert abs(balanced[0] - balanced[1]) < 1e-4  # undecayed: equal pull

    monkeypatch.setenv("CINEMATCH_DECAY", "1")
    decayed = taste.build_taste_vector(rated)
    assert decayed[0] > 0.9 and abs(decayed[1]) < 0.45  # recent taste dominates


def test_set_user_rating_persists_imported_dates(db):
    add_user(db)
    add_movie(db, 42)
    stamp = datetime(2023, 7, 14, 12, 0, 0)
    from services.ratings import set_user_rating

    set_user_rating(db, 1, 42, 4.5, logged_at=stamp)
    logged = db.query(LoggedMovie).filter_by(user_id=1, tmdb_id=42).one()
    assert logged.logged_at == stamp


def test_search_nudge_uses_the_best_taste_mode(db):
    add_user(db)
    add_movie(db, 1, vector=(1.0, 0.0))
    db.add(LoggedMovie(user_id=1, tmdb_id=1, rating=5.0))
    db.commit()

    vec = taste.build_taste_vector(taste.load_rated_movies(db, 1))
    mode_drama = taste.TasteMode(
        label="Drama",
        vector=np.array([0.8, 0.6], dtype=np.float32),
        size=1,
        subniches=("Drama",),
        genres=("Drama",),
    )
    emb = np.array([0.6, 0.8], dtype=np.float32)  # aligned with the mode, not the global vector
    global_sim = search_svc._taste_nudge(vec, None, emb)
    with_mode = search_svc._taste_nudge(vec, [mode_drama], emb)
    assert with_mode == pytest.approx(0.96, abs=1e-5)  # dot((0.8,0.6),(0.6,0.8))
    assert with_mode > global_sim


def test_subniche_affinity_is_support_gated_and_bounded(db):
    add_user(db)
    loved = ThemeCluster(slug="subniche-01-grief", label="Grief dramas · Loss · Memory")
    disliked = ThemeCluster(slug="subniche-02-slapstick", label="Slapstick comedy · Pranks")
    db.add_all([loved, disliked])
    db.flush()

    def add_with_niche(tmdb_id, cluster, weight=0.6):
        add_movie(db, tmdb_id)
        db.add(FilmSubniche(tmdb_id=tmdb_id, cluster_id=cluster.id, weight=weight))

    # GATE is 3: two rated subnichelike films are not enough evidence.
    add_with_niche(1, loved)
    add_with_niche(2, loved)
    db.add(LoggedMovie(user_id=1, tmdb_id=1, rating=5.0))
    db.add(LoggedMovie(user_id=1, tmdb_id=2, rating=5.0))
    db.commit()

    signals = features.load_user_signals(db, 1)
    assert not signals.has_subniches
    assert features.subniche_match([("Grief dramas · Loss · Memory", 0.6)], signals) == (None, [])

    # A third rated film crosses the gate; disliked films pull their niche down.
    add_with_niche(3, loved)
    add_with_niche(4, disliked)
    add_with_niche(5, loved)
    db.add(LoggedMovie(user_id=1, tmdb_id=3, rating=5.0))
    db.add(LoggedMovie(user_id=1, tmdb_id=4, rating=1.0))
    db.add(LoggedMovie(user_id=1, tmdb_id=5, rating=5.0))
    db.commit()

    signals = features.load_user_signals(db, 1)
    assert signals.has_subniches
    score, labels = features.subniche_match(
        [("Grief dramas · Loss · Memory", 0.6)], signals
    )
    assert score is not None and 0.5 < score <= 1.0
    assert labels == ["Grief dramas · Loss · Memory"]
    other, _ = features.subniche_match([("Slapstick comedy · Pranks", 0.5)], signals)
    assert other is not None and other < 0.5
    # Faint or absent memberships never fabricate a match.
    assert features.subniche_match([], signals) == (None, [])
    assert features.subniche_match([("Unknown niche", 0.9)], signals)[0] is None


def test_subniche_likes_count_toward_the_gate(db):
    add_user(db)
    cluster = ThemeCluster(slug="subniche-03-heist", label="Heist thrillers · Capers")
    db.add(cluster)
    db.flush()
    for index in (1, 2, 3):
        add_movie(db, index)
        db.add(FilmSubniche(tmdb_id=index, cluster_id=cluster.id, weight=0.5))
    db.add(LoggedMovie(user_id=1, tmdb_id=1, rating=4.5))
    db.add(UserLike(user_id=1, tmdb_id=2))
    db.add(UserLike(user_id=1, tmdb_id=3))
    db.commit()

    signals = features.load_user_signals(db, 1)
    assert signals.subniche_support >= 3
    score, _ = features.subniche_match([("Heist thrillers · Capers", 0.5)], signals)
    assert score is not None and 0.5 < score <= 1.0


def test_modes_split_two_distinct_taste_clusters(db):
    add_user(db)
    # 8 action films along e1, 8 comedies along e2 — two clean phases.
    for index in range(1, 9):
        add_movie(db, index, genres="Action", vector=(1.0, 0.0))
        db.add(LoggedMovie(user_id=1, tmdb_id=index, rating=5.0))
        add_movie(db, index + 100, genres="Comedy", vector=(0.0, 1.0))
        db.add(LoggedMovie(user_id=1, tmdb_id=index + 100, rating=5.0))
    db.commit()

    rated = taste.load_rated_movies(db, 1)
    modes = taste.build_taste_modes(rated)
    assert len(modes) >= 2
    labels = {mode.label for mode in modes}
    assert "Action" in labels and "Comedy" in labels
    for mode in modes:
        assert abs(float(np.linalg.norm(mode.vector)) - 1.0) < 1e-4
        assert mode.size >= taste.MODE_MIN_CLUSTER


def test_modes_refuse_unimodal_and_tiny_histories(db):
    add_user(db)
    # 20 films in one tight direction (tiny jitter): one taste, not many.
    for index in range(1, 21):
        vector = (1.0, 0.0) if index % 2 else (1.0, 0.05)
        add_movie(db, index, genres="Drama", vector=vector)
        db.add(LoggedMovie(user_id=1, tmdb_id=index, rating=5.0))
    db.commit()

    rated = taste.load_rated_movies(db, 1)
    assert taste.build_taste_modes(rated) == []

    # A 6-film history is below MODE_MIN_FILMS even if it looks promising.
    assert taste.build_taste_modes(rated[:6]) == []


def test_rank_feed_mode_carries_content_and_explanation(db):
    add_user(db)
    for index in range(1, 9):
        add_movie(db, index, genres="Action", vector=(1.0, 0.0))
        db.add(LoggedMovie(user_id=1, tmdb_id=index, rating=5.0))
        add_movie(db, index + 100, genres="Comedy", vector=(0.0, 1.0))
        db.add(LoggedMovie(user_id=1, tmdb_id=index + 100, rating=5.0))
    # A new comedy candidate: the comedy mode should own its content score.
    add_movie(db, 900, genres="Comedy", vector=(0.0, 1.0))
    db.commit()

    candidates = [db.query(MovieCache).filter_by(tmdb_id=900).one()]
    results = ranking.rank_feed(db, 1, candidates, 5)
    assert results and results[0]["movie"].tmdb_id == 900
    assert results[0]["signals"].get("mode") == "Comedy"
    reason = results[0]["reason"]
    assert "Comedy" in reason and "side" in reason

    # Ablation removes modes and the mode-specific fields with them.
    import importlib
    import os

    import services.ranker_config as rc

    os.environ["CINEMATCH_DISABLE"] = "modes"
    try:
        importlib.reload(rc)
        importlib.reload(ranking)
        ablated = ranking.rank_feed(db, 1, candidates, 5)
        assert "mode" not in ablated[0]["signals"]
    finally:
        os.environ.pop("CINEMATCH_DISABLE", None)
        importlib.reload(rc)
        importlib.reload(ranking)


def test_feedback_suppression_is_reversible(db):
    add_user(db)
    db.commit()
    feedback_svc.set_feedback(db, 1, 42, "not_interested")
    assert feedback_svc.suppressed_ids(db, 1) == {42}
    assert db.query(UserFeedback).count() == 1

    feedback_svc.clear_feedback(db, 1, 42, "not_interested")
    assert feedback_svc.suppressed_ids(db, 1) == set()


def test_impressions_are_deduped_per_call(db):
    add_user(db)
    db.commit()
    assert feedback_svc.record_impressions(db, 1, [1, 1, 2, 3], session_id="s") == 3
    assert db.query(FeedImpression).count() == 3
    assert feedback_svc.record_impressions(db, 1, [3, 4], session_id="s") == 1
    assert db.query(FeedImpression).count() == 4
