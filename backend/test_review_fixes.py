"""Regression tests for the review pass: relationship search, actor catalogs,
and genuinely refreshed feeds."""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base, FilmNeighbor, FilmPerson, MovieCache, User
from routers.movies import _neighbor_reason, _neighbor_shelf, get_feed
from routers.people import get_person
from services import search


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def add_movie(db, tmdb_id, title, *, collection_id=None, themes=None, keywords=None):
    movie = MovieCache(
        tmdb_id=tmdb_id,
        title=title,
        overview=f"{title} overview",
        genres="Action,Crime",
        popularity=50.0 + tmdb_id,
        vote_average=8.0,
        vote_count=1000,
        release_year=2008,
        collection_id=collection_id,
        themes=json.dumps(themes or []),
        keywords=json.dumps(keywords or []),
        embedding=json.dumps([1.0, 0.0]),
    )
    db.add(movie)
    return movie


def add_credit(db, tmdb_id, person_id, name, role):
    db.add(
        FilmPerson(
            tmdb_id=tmdb_id,
            person_id=person_id,
            name=name,
            role=role,
            ordering=0 if role == "cast" else None,
            source="tmdb",
        )
    )


def test_relationship_evidence_prefers_franchise_over_loose_similarity(db):
    anchor = add_movie(
        db,
        1,
        "The Dark Knight",
        collection_id=263,
        themes=[{"id": "lb:epic-heroes", "label": "Epic heroes"}],
        keywords=["superhero", "based on comic", "vigilante"],
    )
    sequel = add_movie(
        db,
        2,
        "The Dark Knight Rises",
        collection_id=263,
        themes=[{"id": "lb:epic-heroes", "label": "Epic heroes"}],
        keywords=["superhero", "based on comic", "vigilante"],
    )
    cousin = add_movie(
        db,
        3,
        "Batman Beyond",
        collection_id=999,
        keywords=["superhero"],
    )
    unrelated = add_movie(db, 4, "Pulp Fiction", keywords=["neo-noir"])
    add_credit(db, 1, 525, "Christopher Nolan", "director")
    add_credit(db, 2, 525, "Christopher Nolan", "director")
    add_credit(db, 1, 3894, "Christian Bale", "cast")
    add_credit(db, 2, 3894, "Christian Bale", "cast")
    db.commit()

    movies = db.query(MovieCache).all()
    context = search._anchor_relationship_context(db, anchor, movies)
    sequel_relation, sequel_signal = search._relationship_score(sequel, context)
    cousin_relation, cousin_signal = search._relationship_score(cousin, context)
    unrelated_relation, unrelated_signal = search._relationship_score(
        unrelated, context
    )

    assert sequel_signal == "collection"
    assert cousin_signal == "franchise"
    assert unrelated_signal is None
    assert sequel_relation > cousin_relation > unrelated_relation


def test_neighbor_shelf_prefers_local_behavior_over_hybrid(db):
    anchor = add_movie(db, 1, "The Dark Knight", collection_id=263)
    local = add_movie(db, 2, "Batman Begins", collection_id=263)
    fallback = add_movie(db, 3, "Pulp Fiction")
    db.add(
        FilmNeighbor(
            tmdb_id=anchor.tmdb_id,
            neighbor_tmdb_id=local.tmdb_id,
            score=0.55,
            rank=1,
            source="behavior_v1",
            evidence=json.dumps(
                {
                    "signals": {
                        "origin": "local_behavior",
                        "hop": 1,
                        "co_likers": 15,
                        "co_raters": 18,
                    }
                }
            ),
        )
    )
    db.add(
        FilmNeighbor(
            tmdb_id=anchor.tmdb_id,
            neighbor_tmdb_id=fallback.tmdb_id,
            score=0.90,
            rank=1,
            source="hybrid_v1",
            evidence=json.dumps({"signals": {"theme": 0.5}}),
        )
    )
    db.commit()

    shelf = _neighbor_shelf(db, anchor, limit=2)
    assert [movie["tmdb_id"] for movie in shelf] == [local.tmdb_id, fallback.tmdb_id]
    assert shelf[0]["reason"] == (
        "15 people who liked The Dark Knight also liked this"
    )
    assert _neighbor_reason(
        json.dumps(
            {
                "signals": {
                    "origin": "local_behavior",
                    "hop": 2,
                    "co_likers": 4,
                }
            }
        ),
        "The Dark Knight",
    ) == "Connected to The Dark Knight through 2 hops of local viewer behavior"


def test_person_catalog_groups_roles_and_orders_newest_first(db):
    user = User(user_id=1, username="tester", password_hash="x", is_seed=0)
    db.add(user)
    add_movie(db, 10, "Older Film")
    add_movie(db, 11, "Newer Film")
    db.query(MovieCache).filter(MovieCache.tmdb_id == 10).one().release_year = 1999
    db.query(MovieCache).filter(MovieCache.tmdb_id == 11).one().release_year = 2024
    add_credit(db, 10, 77, "A Performer", "cast")
    add_credit(db, 11, 77, "A Performer", "cast")
    add_credit(db, 11, 77, "A Performer", "writer")
    db.commit()

    payload = get_person(77, db=db, user=user)

    assert payload["name"] == "A Performer"
    assert payload["film_count"] == 2
    assert [film["tmdb_id"] for film in payload["films"]] == [11, 10]
    assert payload["films"][0]["roles"] == ["writer", "cast"]
    assert {role["role"] for role in payload["roles"]} == {"cast", "writer"}


def test_feed_exclude_returns_a_different_page(db):
    user = User(user_id=1, username="tester", password_hash="x", is_seed=0)
    db.add(user)
    for tmdb_id in range(1, 9):
        add_movie(db, tmdb_id, f"Film {tmdb_id}")
    db.commit()

    first = get_feed(limit=4, exclude=None, db=db, user=user)
    first_ids = {film["tmdb_id"] for film in first["results"]}
    assert len(first_ids) == 4

    refreshed = get_feed(
        limit=4,
        exclude=",".join(str(tmdb_id) for tmdb_id in first_ids),
        db=db,
        user=user,
    )
    refreshed_ids = {film["tmdb_id"] for film in refreshed["results"]}
    assert refreshed_ids
    assert first_ids.isdisjoint(refreshed_ids)
