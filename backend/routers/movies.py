import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.database import (
    FilmNeighbor,
    FilmPerson,
    FilmSubniche,
    LoggedMovie,
    MovieCache,
    Review,
    ThemeCluster,
    UserLike,
    WatchlistItem,
    WatchedMovie,
    get_db,
)
from routers.deps import format_movie, resolve_user
from services import collab, embeddings, ranking, search as search_svc
from services import feedback as feedback_svc
from services import sentiment as sentiment_svc
from services import taste as taste_svc

router = APIRouter(prefix="/movies", tags=["movies"])


def _json_value(value, default):
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _local_movie_payload(db, cached):
    """Build the detail payload from local rows only (no TMDB request)."""
    payload = format_movie(cached)
    assets = _json_value(cached.assets, {})
    if isinstance(assets, dict):
        payload.update(assets)
    payload["runtime"] = cached.runtime
    payload["release_date"] = payload.get("release_date")
    payload["vote_count"] = cached.vote_count
    payload["original_language"] = cached.original_language
    payload["collection_id"] = cached.collection_id
    payload["imdb_id"] = cached.imdb_id
    payload["letterboxd_slug"] = cached.letterboxd_slug

    keywords = _json_value(cached.keywords, [])
    payload["keywords"] = [str(item) for item in keywords if item] if isinstance(keywords, list) else []
    theme_items = _json_value(cached.themes, [])
    theme_labels = []
    for item in theme_items if isinstance(theme_items, list) else []:
        if isinstance(item, dict):
            label = item.get("label") or item.get("id")
        else:
            label = item
        if label and str(label) not in theme_labels:
            theme_labels.append(str(label))
    payload["themes"] = theme_labels

    people = (
        db.query(FilmPerson)
        .filter(FilmPerson.tmdb_id == cached.tmdb_id)
        .order_by(FilmPerson.role, FilmPerson.ordering)
        .all()
    )
    directors = []
    writers = []
    cast = []
    for person in people:
        entry = {
            "id": person.person_id,
            "name": person.name,
            "profile_path": person.profile_path,
        }
        if person.role == "director":
            directors.append(entry)
        elif person.role == "writer":
            writers.append(entry)
        elif person.role == "cast":
            cast.append(entry)
    payload["directors"] = directors
    payload["writers"] = writers
    payload["cast"] = cast[:12]

    subniches = (
        db.query(ThemeCluster.label, FilmSubniche.weight)
        .join(FilmSubniche, FilmSubniche.cluster_id == ThemeCluster.id)
        .filter(FilmSubniche.tmdb_id == cached.tmdb_id)
        .order_by(FilmSubniche.weight.desc())
        .limit(3)
        .all()
    )
    payload["subniches"] = [label for label, _weight in subniches]
    # Theme clusters are display metadata only.  The relationship graph is
    # built from local ratings/likes/watches; the theme fallback must not be
    # mistaken for the source of the "more like this" edges.
    payload["subniche_source"] = "theme_metadata"
    payload.setdefault("images", {"backdrops": [], "posters": []})
    payload.setdefault("videos", [])
    return payload


def _sync_person_profiles(db, tmdb_id, credits):
    """Persist TMDB portrait URLs onto the local credit rows.

    An empty string records "checked, no portrait available" so a film is not
    fetched again just because one cast member has no image on TMDB.
    """
    profiles = {}
    for group in ("cast", "directors", "writers"):
        for person in credits.get(group) or []:
            person_id = person.get("id")
            if person_id:
                profiles[int(person_id)] = person.get("profile_path") or ""

    rows = db.query(FilmPerson).filter(FilmPerson.tmdb_id == tmdb_id).all()
    for row in rows:
        row.profile_path = profiles.get(row.person_id, "")
    db.commit()


def _assets_from_full(full):
    keys = (
        "tagline", "status", "release_date", "backdrop_path",
        "spoken_languages", "production_countries", "production_companies",
        "budget", "revenue", "homepage", "certification", "videos", "images",
    )
    assets = {
        key: full.get(key)
        for key in keys
        if full.get(key) not in (None, "", [], {})
    }
    return json.dumps(assets, ensure_ascii=False)


def _neighbor_reason(evidence_raw, anchor_title):
    evidence = _json_value(evidence_raw, {})
    signals = evidence.get("signals") if isinstance(evidence, dict) else {}
    signals = signals or {}

    if signals.get("origin") == "local_behavior":
        hop = int(signals.get("hop") or 1)
        co_raters = int(signals.get("co_raters") or 0)
        co_likers = int(signals.get("co_likers") or 0)
        co_watchers = int(signals.get("co_watchers") or 0)
        if hop > 1:
            return (
                f"Connected to {anchor_title} through "
                f"{hop} hops of local viewer behavior"
            )
        if co_likers >= 2:
            return f"{co_likers} people who liked {anchor_title} also liked this"
        if co_raters >= 2:
            return f"{co_raters} people who rated {anchor_title} also rated this"
        if co_watchers >= 2:
            return (
                f"{co_watchers} people who watched {anchor_title} "
                "also watched this"
            )
        if signals.get("collection_prior"):
            return f"Local viewers connect this to {anchor_title}'s franchise"
        return f"Linked by local viewers of {anchor_title}"

    if signals.get("collection", 0) > 0:
        return f"Continues the same franchise as {anchor_title}"
    if signals.get("director", 0) > 0:
        return f"From the same director as {anchor_title}"
    if signals.get("cast", 0) >= 0.15:
        return f"Shares cast with {anchor_title}"
    if signals.get("theme", 0) >= 0.25:
        return f"Shares its themes with {anchor_title}"
    if signals.get("writer", 0) > 0:
        return f"From the same writing team as {anchor_title}"
    if signals.get("subniche", 0) > 0:
        return f"In the same theme cluster as {anchor_title}"
    return f"Related to {anchor_title}"


def _neighbor_shelf(db, cached, limit=10):
    """Behavior edges lead; metadata fills only the slots behavior cannot.

    ``behavior_v1`` is built from local ratings/likes/watches.  ``hybrid_v1``
    remains the cold-start fallback for films with too little local evidence.
    """
    selected = []
    seen = set()

    def add_rows(source):
        rows = (
            db.query(FilmNeighbor, MovieCache)
            .join(MovieCache, MovieCache.tmdb_id == FilmNeighbor.neighbor_tmdb_id)
            .filter(
                FilmNeighbor.tmdb_id == cached.tmdb_id,
                FilmNeighbor.source == source,
            )
            .order_by(FilmNeighbor.rank)
            .limit(limit)
            .all()
        )
        for edge, movie in rows:
            if movie.tmdb_id in seen:
                continue
            seen.add(movie.tmdb_id)
            selected.append(
                (
                    edge,
                    movie,
                )
            )
            if len(selected) >= limit:
                break

    add_rows("behavior_v1")
    if len(selected) < limit:
        add_rows("hybrid_v1")

    return [
        format_movie(
            movie,
            round(edge.score * 100.0, 1),
            _neighbor_reason(edge.evidence, cached.title),
        )
        for edge, movie in selected[:limit]
    ]


def _user_review_compounds(db, user_id):
    return {
        row[0]: row[1]
        for row in db.query(Review.tmdb_id, Review.sentiment_compound)
        .filter(Review.user_id == user_id)
        .all()
    }


def _taste_vector_for(db, user_id):
    """Taste vector built from this user's rated films + review sentiment."""
    compounds = _user_review_compounds(db, user_id)
    rated_view = taste_svc.load_rated_movies(db, user_id)
    return taste_svc.build_taste_vector(rated_view, compounds), rated_view


@router.get("/feed")
def get_feed(
    limit: int = Query(16, ge=4, le=60),
    exclude: str | None = Query(None, max_length=1200),
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    all_movies = db.query(MovieCache).all()
    if not all_movies:
        return {"feed_type": "empty", "results": []}

    # Exclude anything the user already rated or watched (Letterboxd history).
    # Both filters are scoped to this user — the previous watched query had no
    # user filter at all, so one account's history excluded films for everyone.
    rated_ids = {
        r[0]
        for r in db.query(LoggedMovie.tmdb_id).filter(LoggedMovie.user_id == user.user_id).all()
    }
    watched_ids = {
        r[0]
        for r in db.query(WatchedMovie.tmdb_id)
        .filter(
            WatchedMovie.user_id == user.user_id,
            WatchedMovie.tmdb_id.isnot(None),
        )
        .all()
    }
    liked_ids = {
        r[0]
        for r in db.query(UserLike.tmdb_id)
        .filter(UserLike.user_id == user.user_id)
        .all()
    }
    candidates = [
        m
        for m in all_movies
        if m.tmdb_id not in rated_ids | watched_ids | liked_ids
    ]
    suppressed = feedback_svc.suppressed_ids(db, user.user_id)
    if suppressed:
        candidates = [m for m in candidates if m.tmdb_id not in suppressed]

    # Refreshing the feed must actually change it. The frontend passes the
    # films currently on screen; excluding them is deterministic, cheap, and
    # keeps the ranking model itself untouched.
    if exclude:
        excluded_ids = {
            int(token)
            for token in (part.strip() for part in exclude.split(","))
            if token.isdigit()
        }
        if excluded_ids:
            candidates = [m for m in candidates if m.tmdb_id not in excluded_ids]

    # Loaded once: the summary and the ranking both need these.
    compounds = _user_review_compounds(db, user.user_id)
    rated_view = taste_svc.load_rated_movies(db, user.user_id)

    if len(rated_ids) >= 1 and candidates:
        results = ranking.rank_feed(
            db,
            user.user_id,
            candidates,
            limit,
            rated_view=rated_view,
            compounds=compounds,
        )
        feed_type = "personalized"
    else:
        results = ranking.cold_start_feed(candidates, limit)
        feed_type = "discover"

    return {
        "feed_type": feed_type,
        "profile_summary": taste_svc.summarize(rated_view, compounds)
        if feed_type == "personalized"
        else "Rate a few films and recommendations will adapt to your taste.",
        "results": [
            format_movie(r["movie"], r["score"], r["reason"], {"signals": r.get("signals")})
            for r in results
        ],
    }


@router.get("/search")
def search_movies(
    q: str = Query(..., min_length=1),
    limit: int = Query(16, ge=1, le=60),
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    all_movies = db.query(MovieCache).all()
    if not all_movies:
        return {"query": q, "mode": "none", "score_label": None,
                "reference_title": None, "results": []}

    taste_vec, _ = _taste_vector_for(db, user.user_id)
    # Search-side taste modes were gated out (reports/eval_search_gate.md):
    # the 0.12–0.15 nudge is structurally too weak to reorder results, and
    # the per-request k-means fit bought nothing measurable. The dormant
    # `taste_modes` hook stays in search.search for a future, properly
    # gated personalization experiment.
    exact_movie = search_svc.find_exact_movie(q, all_movies)
    search_limit = max(1, limit - 1) if exact_movie else limit
    mode, label, results = search_svc.search(
        db, q, all_movies, taste_vec, search_limit, anchor=exact_movie
    )

    exact_payload = None
    payload_results = []
    if exact_movie is not None:
        exact_payload = format_movie(
            exact_movie,
            None,
            "Exact title match",
            {"match_type": "title"},
        )
        payload_results.append(exact_payload)
    payload_results.extend(
        format_movie(r["movie"], r["score"], r["reason"]) for r in results
    )

    reference_title = None
    if exact_movie is not None:
        reference_title = exact_movie.title
    elif mode == "anchor":
        anchor = search_svc.detect_anchor(q, all_movies)
        reference_title = anchor.title if anchor else None

    return {
        "query": q,
        "mode": mode,
        "score_label": label,
        "reference_title": reference_title,
        "exact": exact_payload,
        "results": payload_results,
    }


@router.get("/engine-status")
def engine_status(db: Session = Depends(get_db)):
    """Transparency endpoint: what's actually powering recommendations."""
    cf = collab.cf_state(db)
    catalog = db.query(MovieCache).count()
    embedded = db.query(MovieCache).filter(MovieCache.embedding.isnot(None)).count()
    embedded_valid = sum(
        1
        for (emb,) in db.query(MovieCache.embedding).all()
        if embeddings.decode_embedding(emb) is not None
    )
    try:
        signature = embeddings.active_signature(db) or "not-initialized"
    except Exception:
        signature = "unavailable"
    behavior_neighbors = (
        db.query(FilmNeighbor)
        .filter(FilmNeighbor.source == "behavior_v1")
        .count()
    )
    hybrid_neighbors = (
        db.query(FilmNeighbor)
        .filter(FilmNeighbor.source == "hybrid_v1")
        .count()
    )
    try:
        behavior_clusters = (
            db.execute(text("select count(*) from behavior_clusters")).scalar() or 0
        )
        behavior_subniched_movies = (
            db.execute(
                text(
                    "select count(distinct tmdb_id) "
                    "from film_behavior_subniches"
                )
            ).scalar()
            or 0
        )
    except Exception:
        behavior_clusters = 0
        behavior_subniched_movies = 0
    return {
        "catalog_size": catalog,
        "embedded_movies": min(embedded, embedded_valid),
        "embedding_backend": signature.split(":")[0],
        "embedding_signature": signature,
        "film_neighbors": db.query(FilmNeighbor).count(),
        "behavior_neighbors": behavior_neighbors,
        "behavior_neighbor_films": (
            db.query(FilmNeighbor.tmdb_id)
            .filter(FilmNeighbor.source == "behavior_v1")
            .distinct()
            .count()
        ),
        "hybrid_neighbors": hybrid_neighbors,
        "behavior_clusters": behavior_clusters,
        "behavior_subniched_movies": behavior_subniched_movies,
        "relationship_graph": "behavior_v1 first, hybrid_v1 cold-start fallback",
        "theme_clusters": db.query(ThemeCluster).count(),
        "subniched_movies": db.query(FilmSubniche.tmdb_id).distinct().count(),
        "collaborative_filtering": cf,
        "sentiment_model": "VADER (Hutto & Gilbert, 2014)",
    }


@router.get("/profile-summary")
def profile_summary(db: Session = Depends(get_db), user=Depends(resolve_user)):
    _, rated_view = _taste_vector_for(db, user.user_id)
    compounds = _user_review_compounds(db, user.user_id)
    reviews = db.query(Review).filter(Review.user_id == user.user_id).all()
    return {
        "summary": taste_svc.summarize(rated_view, compounds),
        "genres": taste_svc.genre_affinity(rated_view, compounds, limit=5),
        "sentiment": sentiment_svc.aggregate(reviews),
    }


@router.get("/tmdb-search")
def tmdb_search(q: str = Query(..., min_length=1)):
    from services import tmdb

    return tmdb.search_movies(q, limit=8)


@router.get("/{tmdb_id}")
def get_movie_detail(
    tmdb_id: int,
    include_match: bool = Query(True),
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    from services import tmdb
    from services.ratings import ensure_cached_movie

    cached = db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).first()
    full = None
    if cached is None:
        # Cache miss only: a new release discovered through TMDB search is
        # materialized once, then served locally on every subsequent request.
        full = tmdb.get_movie_full(tmdb_id)
        details = full
        if not details:
            try:
                details = tmdb.get_movie_details(tmdb_id)
            except tmdb.TMDBUnavailable:
                raise HTTPException(
                    status_code=503,
                    detail="TMDB is unreachable right now — try again shortly.",
                )
        if not details:
            raise HTTPException(status_code=404, detail="Movie not found")
        cached = ensure_cached_movie(db, details)
        if full:
            cached.collection_id = full.get("collection_id")
            cached.imdb_id = full.get("imdb_id")
            cached.runtime = full.get("runtime")
            cached.original_language = full.get("original_language")
            cached.vote_count = full.get("vote_count")
            cached.assets = _assets_from_full(full)
            cached.keywords = json.dumps(full.get("keywords") or [], ensure_ascii=False)
        db.commit()

    payload = _local_movie_payload(db, cached)
    if full:
        # First response for a cold film can use the credits already in hand;
        # the relationship shelf is still local/empty until refresh.
        payload["cast"] = payload.get("cast") or full.get("cast") or []
        payload["directors"] = payload.get("directors") or full.get("directors") or []
        payload["writers"] = payload.get("writers") or full.get("writers") or []
    elif (
        db.query(FilmPerson.id)
        .filter(
            FilmPerson.tmdb_id == tmdb_id,
            FilmPerson.role == "cast",
            FilmPerson.profile_path.is_(None),
        )
        .first()
        is not None
    ):
        # The catalog predates portrait storage. Fetch credits once per film,
        # persist the URLs locally, and serve every later request from SQLite.
        credits = tmdb.get_movie_credits(tmdb_id)
        if credits:
            _sync_person_profiles(db, tmdb_id, credits)
            payload = _local_movie_payload(db, cached)

    shelf = _neighbor_shelf(db, cached, limit=10)
    if shelf:
        payload["similar"] = shelf
    else:
        # Only films not yet graphed fall back to the bounded local anchor
        # ranking; this is not a TMDB request.
        try:
            all_movies = db.query(MovieCache).all()
            _, _, related = search_svc.search(
                db, cached.title, all_movies, None, 10, anchor=cached
            )
            if related:
                payload["similar"] = [
                    format_movie(result["movie"], result["score"], result["reason"])
                    for result in related[:10]
                ]
        except Exception:
            pass

    logged = (
        db.query(LoggedMovie)
        .filter(LoggedMovie.user_id == user.user_id, LoggedMovie.tmdb_id == tmdb_id)
        .first()
    )
    payload["user_rating"] = logged.rating if logged else None
    payload["logged_at"] = logged.logged_at.isoformat() if logged and logged.logged_at else None
    payload["in_watchlist"] = (
        db.query(WatchlistItem.id)
        .filter(
            WatchlistItem.user_id == user.user_id,
            WatchlistItem.tmdb_id == tmdb_id,
        )
        .first()
        is not None
    )

    review = (
        db.query(Review)
        .filter(Review.user_id == user.user_id, Review.tmdb_id == tmdb_id)
        .first()
    )
    payload["user_review"] = (
        {
            "content": review.content,
            "compound": round(review.sentiment_compound, 3),
            "label": sentiment_svc.label(review.sentiment_compound),
            "created_at": review.created_at.isoformat() if review.created_at else None,
        }
        if review
        else None
    )

    # Explain a film the same way the feed does. A film already in the archive
    # leads with the rating the viewer gave it, not a prediction.
    if include_match and not logged:
        has_ratings = (
            db.query(LoggedMovie)
            .filter(LoggedMovie.user_id == user.user_id)
            .count()
            > 0
        )
        if has_ratings:
            try:
                ranked = ranking.rank_feed(db, user.user_id, [cached], 1)
                if ranked:
                    payload["match"] = {
                        "score": ranked[0]["score"],
                        "reason": ranked[0]["reason"],
                        "signals": ranked[0].get("signals") or {},
                    }
            except Exception:
                # The overview still works if the ranking matrix cannot score
                # a brand-new film; the reason is an enhancement, not a gate.
                pass

    return payload
