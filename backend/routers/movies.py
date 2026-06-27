from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from models.database import get_db, MovieCache, LoggedMovie, WatchedMovie
from engine.cinematch import recommend_from_backend
from services import tmdb, nlp
from services.taste_model import generate_mood_context
from difflib import SequenceMatcher
import random

router = APIRouter(prefix="/movies", tags=["movies"])

DEFAULT_FEED_TITLES = [
    "Fight Club",
    "Inception",
    "The Prestige",
    "Interstellar",
    "Batman Begins",
    "Shutter Island",
    "The Notebook",
    "Titanic",
    "Boyhood",
    "Superbad",
    "The Departed",
    "The Bourne Identity",
]

def format_movie(movie_orm, score=None, reason=None):
    """Convert ORM object to JSON-serializable dict."""
    return {
        "tmdb_id": movie_orm.tmdb_id,
        "title": movie_orm.title,
        "overview": movie_orm.overview,
        "genres": movie_orm.genres.split(",") if movie_orm.genres else [],
        "poster_path": movie_orm.poster_path,
        "release_year": movie_orm.release_year,
        "vote_average": movie_orm.vote_average,
        "score": score,
        "reason": reason,
        "mood_context": generate_mood_context(movie_orm),
        "emotion_tags": nlp.infer_tags(movie_orm)["emotion"],
        "vibe_tags": nlp.infer_tags(movie_orm)["vibe"],
        "tone_tags": nlp.infer_tags(movie_orm)["tone"],
    }

def cache_movie(movie_data, db: Session):
    """Store a TMDB movie object into local cache with text or dense embedding."""
    existing = db.query(MovieCache).filter(MovieCache.tmdb_id == movie_data["tmdb_id"]).first()
    if existing:
        return existing
    embedding_list = nlp.embed_movie(movie_data)
    cached = MovieCache(
        tmdb_id=movie_data["tmdb_id"],
        title=movie_data["title"],
        overview=movie_data["overview"],
        genres=",".join(movie_data["genres"]),
        poster_path=movie_data["poster_path"],
        release_year=movie_data["release_year"],
        popularity=movie_data["popularity"],
        vote_average=movie_data["vote_average"],
        embedding=nlp.embedding_to_json(embedding_list),
    )
    db.add(cached)
    db.commit()
    db.refresh(cached)
    return cached

def movie_watch_key(movie):
    title = nlp.clean_title(movie.title)
    return (title, movie.release_year) if title else None

def get_cached_movie_by_title(title: str, db: Session):
    """Check cache or fetch from TMDB search fallback."""
    cleaned = title.lower().strip()
    cached = db.query(MovieCache).all()
    for c in cached:
        if c.title and c.title.lower().strip() == cleaned:
            return c
    results = tmdb.search_movies(title, limit=1)
    if results:
        return cache_movie(results[0], db)
    return None

def hydrate_similar_movies(reference_movie, db: Session):
    """Pull TMDB similar movies into the cache so title searches have better neighbors."""
    try:
        for title in nlp.CURATED_SIMILARITY_BOOSTS.get(reference_movie.title or "", {}):
            get_cached_movie_by_title(title, db)
        for movie_data in tmdb.get_similar_movies(reference_movie.tmdb_id, limit=20):
            cache_movie(movie_data, db)
    except Exception:
        return

def find_or_fetch_reference_movie(query: str, candidate_movies, db: Session):
    """Find a title anchor locally, or fetch one from TMDB for direct title searches."""
    query_tokens = nlp.tokenize(query)
    intent_trigger_tokens = set()
    for config in nlp.SEARCH_INTENTS.values():
        intent_trigger_tokens |= config["triggers"]
    non_intent_tokens = query_tokens - intent_trigger_tokens
    mood_only_query = bool(query_tokens) and not non_intent_tokens

    ref_movie = nlp.find_reference_movie(query, candidate_movies)
    if ref_movie and not mood_only_query:
        return ref_movie

    cleaned_query = nlp.clean_title(query)
    if not cleaned_query or len(cleaned_query) < 3:
        return None

    contained_matches = []
    for movie in candidate_movies:
        cleaned_title = nlp.clean_title(movie.title)
        title_token_count = len(cleaned_title.split())
        if cleaned_title and title_token_count >= 2 and cleaned_title in cleaned_query:
            contained_matches.append((len(cleaned_title), movie))
    if contained_matches:
        return max(contained_matches, key=lambda item: item[0])[1]

    search_terms = [query]
    keyword_query = " ".join(nlp.tokenize(query))
    if keyword_query and keyword_query != query:
        search_terms.append(keyword_query)

    try:
        for search_term in search_terms:
            cleaned_search = nlp.clean_title(search_term)
            for movie_data in tmdb.search_movies(search_term, limit=5):
                cleaned_title = nlp.clean_title(movie_data.get("title", ""))
                if not cleaned_title:
                    continue
                title_token_count = len(cleaned_title.split())
                title_ratio = SequenceMatcher(None, cleaned_search, cleaned_title).ratio()
                if mood_only_query:
                    continue
                if (
                    cleaned_search == cleaned_title
                    or title_ratio >= 0.85
                    or (title_token_count >= 2 and cleaned_title in cleaned_query)
                ):
                    return cache_movie(movie_data, db)
    except Exception:
        return None

    return None

@router.get("/feed")
def get_feed(limit: int = Query(16, ge=4, le=100), db: Session = Depends(get_db)):
    """Fetch user dashboard personalized movie recommendation feed using advanced taste model.

    Returns an even-numbered, randomized selection (when possible) so the UI grid
    displays balanced rows. Default `limit` increased to 16 for fuller grids.
    """
    # Ensure the DB has seed cache entries for curated titles
    for title in DEFAULT_FEED_TITLES:
        get_cached_movie_by_title(title, db)

    all_movies = db.query(MovieCache).all()
    logged = db.query(LoggedMovie).all()
    watched = db.query(WatchedMovie).all()

    # Normalize limit to an even number for nicer grid layouts
    if limit % 2 != 0:
        limit += 1

    # Cold start: show curated + random films (shuffled each request)
    if not logged:
        default_items = [m for m in all_movies if m.title in DEFAULT_FEED_TITLES]
        # Fill out to `limit` with random picks if needed
        remaining_pool = [m for m in all_movies if m not in default_items]
        random.shuffle(remaining_pool)
        while len(default_items) < limit and remaining_pool:
            default_items.append(remaining_pool.pop())
        random.shuffle(default_items)
        default_items = default_items[:limit]
        return {
            "feed_type": "discover_default",
            "results": [format_movie(m, score=None, reason="Popular curated films - log movies to personalize your feed") for m in default_items]
        }
    
    # Score all unwatched movies with the CineMatch ML engine.
    logged_ids = {m.tmdb_id for m in logged}
    watched_ids = {m.tmdb_id for m in watched if m.tmdb_id}
    watched_keys = {
        (nlp.clean_title(m.title), m.release_year)
        for m in watched
        if m.title
    }
    candidate_movies = []
    for movie in all_movies:
        key = movie_watch_key(movie)
        if movie.tmdb_id in logged_ids or movie.tmdb_id in watched_ids or key in watched_keys:
            continue  # Skip already watched/logged titles
        candidate_movies.append(movie)

    top_results = recommend_from_backend(
        rated_movies=logged,
        candidate_movies=candidate_movies,
        top_n=limit,
    )

    # If the strict ML score floor leaves nothing, keep the UI populated with
    # random unwatched cache entries instead of returning an empty dashboard.
    if not top_results:
        random.shuffle(candidate_movies)
        top_results = [
            {"movie": movie, "score": None, "reason": "Unwatched cached film"}
            for movie in candidate_movies[:limit]
        ]
    
    formatted_results = []
    for item in top_results:
        movie_dict = format_movie(item["movie"], item.get("score"), item.get("reason"))
        formatted_results.append(movie_dict)
    
    return {
        "feed_type": "cinematch_ml",
        "results": formatted_results
    }

@router.get("/search")
def search_movies(q: str = Query(...), limit: int = Query(16, ge=1, le=100), db: Session = Depends(get_db)):
    """Intent-Aware Two-Stage High Precision Search Engine Router End-point."""
    all_movies = db.query(MovieCache).all()
    if not all_movies:
        try:
            tmdb_hits = tmdb.search_movies(q, limit=limit)
            fallback = []
            for hit in tmdb_hits:
                cached = cache_movie(hit, db)
                fallback.append(format_movie(cached, score=None, reason="TMDB title match"))
            return {
                "query": q,
                "intent": "tmdb_fallback",
                "score_label": "tmdb relevance",
                "reference_title": None,
                "results": fallback
            }
        except Exception:
            return {"query": q, "results": []}

    # Identify if searching for an explicit movie title or direct string attributes
    ref_movie = find_or_fetch_reference_movie(q, all_movies, db)

    if ref_movie:
        hydrate_similar_movies(ref_movie, db)
        all_movies = db.query(MovieCache).all()
    else:
        # Pre-hydrate intent titles dynamically to keep candidate items pool rich
        tokens = nlp.tokenize(q)
        for intent, config in nlp.SEARCH_INTENTS.items():
            if config["triggers"] & tokens:
                if intent in nlp.INTENT_SEED_TITLES:
                    for title in nlp.INTENT_SEED_TITLES[intent]:
                        get_cached_movie_by_title(title, db)
                all_movies = db.query(MovieCache).all()
                break

    logged = db.query(LoggedMovie).all()
    taste_profile = nlp.build_taste_profile(logged) if logged else None

    # Execute the deep neural hybrid evaluation sequence pipeline
    ranked_results = nlp.execute_two_stage_rank(
        query_text=q,
        candidate_movies=all_movies,
        reference_movie=ref_movie,
        taste_profile=taste_profile
    )

    # If ranker returned nothing (e.g. cold cache), fallback to TMDB search
    if not ranked_results:
        try:
            tmdb_hits = tmdb.search_movies(q, limit=limit)
            fallback = []
            for hit in tmdb_hits:
                cached = cache_movie(hit, db)
                fallback.append(format_movie(cached, score=None, reason="TMDB fallback"))
            return {
                "query": q,
                "intent": "tmdb_fallback",
                "score_label": "tmdb relevance",
                "reference_title": None,
                "results": fallback
            }
        except Exception:
            # swallow fallback errors and continue to return empty results
            pass

    # If the user clearly queried a known title, surface it first with a strong score
    final_list = []
    if ref_movie:
        try:
            # Ensure we return the canonical cached object if present
            primary = db.query(MovieCache).filter(MovieCache.tmdb_id == ref_movie.tmdb_id).first() or ref_movie
            final_list.append({"movie": primary, "score": 100.0, "reason": "Exact title match"})
        except Exception:
            # Fallback to using the reference as-is
            final_list.append({"movie": ref_movie, "score": 100.0, "reason": "Exact title match"})

    # Append ranked results while avoiding duplicates of the exact match
    for item in ranked_results:
        if ref_movie and item["movie"].tmdb_id == getattr(ref_movie, "tmdb_id", None):
            continue
        final_list.append(item)

    # Format and trim to requested limit
    out_items = final_list[:limit]
    return {
        "query": q,
        "intent": "similarity_anchor" if ref_movie else "contextual_semantic",
        "score_label": "title match + similarity score" if ref_movie else ("transformer context affinity score" if nlp.is_transformer_available() else "lexical match score"),
        "reference_title": ref_movie.title if ref_movie else None,
        "results": [format_movie(item["movie"], item.get("score"), item.get("reason")) for item in out_items]
    }

@router.get("/profile-summary")
def profile_summary(db: Session = Depends(get_db)):
    """Return a natural-language taste profile summary."""
    logged = db.query(LoggedMovie).all()
    return {"summary": nlp.profile_summary(logged)}

@router.get("/tmdb-search")
def tmdb_search(q: str = Query(...)):
    """Search TMDB by title for logging."""
    results = tmdb.search_movies(q, limit=8)
    return results

@router.get("/{tmdb_id}")
def get_movie_detail(tmdb_id: int, db: Session = Depends(get_db)):
    """Get details for a single movie."""
    cached = db.query(MovieCache).filter(MovieCache.tmdb_id == tmdb_id).first()
    if cached:
        return format_movie(cached)
    movie_data = tmdb.get_movie_details(tmdb_id)
    if not movie_data:
        raise HTTPException(status_code=404, detail="Movie not found on fallback TMDB cluster repository")
    cached = cache_movie(movie_data, db)
    return format_movie(cached)
