"""Feed ranking: hybrid of content taste, collaborative filtering, and a
quality prior — diversified with Maximal Marginal Relevance.

Signal weights adapt to evidence:
* collaborative filtering only earns weight in proportion to its confidence
  (how much co-rating support stands behind each prediction),
* content similarity carries the load while the ratings matrix is sparse,
* TMDB quality (vote average/popularity) breaks ties and drives cold-start.
"""

import zlib

import numpy as np

from models.database import Review
from services import collab, features
from services.embeddings import clip_cosine, cosine, decode_embedding
from services.ranker_config import (
    MMR_LAMBDA,
    RANKER_VERSION,
    W_CF_MAX,
    WEIGHTS,
    describe,
    enabled,
)


def _quality_prior(movie) -> float:
    """TMDB vote average [0,10] → shrinkage-adjusted [0,1] prior.

    A 7.9 from 12 votes is not the same evidence as a 7.9 from 20,000 votes,
    so low-count films are shrunk toward the catalog-wide mean (6.8/10).
    """
    rating = float(movie.vote_average or 0.0)
    count = int(movie.vote_count or 0)
    if count > 0:
        prior_mean = 6.8
        weight = 250.0
        rating = (count / (count + weight)) * rating + (
            weight / (count + weight)
        ) * prior_mean
    return max(0.0, min(1.0, rating / 10.0))


def _review_compound_map(db, user_id):
    rows = (
        db.query(Review.tmdb_id, Review.sentiment_compound)
        .filter(Review.user_id == user_id)
        .all()
    )
    return {tmdb_id: compound for tmdb_id, compound in rows}


def _mmr(scores, embeddings, limit, lam=MMR_LAMBDA):
    """Greedy MMR selection. scores: {id: score}, embeddings: {id: vec}.

    Vectorized fast path: unit-normalize the candidate embeddings once and
    maintain the redundancy term incrementally, so each selection step is a
    single matrix-vector product instead of O(n) pairwise cosine calls.
    Falls back to the pairwise loop when vectors are missing or ragged.
    """
    ids = list(scores)
    if not ids or limit <= 0:
        return []

    vectors = [embeddings.get(cid) for cid in ids]
    dims = {len(vec) for vec in vectors if vec is not None}

    if all(vec is not None for vec in vectors) and len(dims) == 1:
        matrix = np.vstack(vectors).astype(np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms
        relevance = np.asarray([scores[cid] for cid in ids], dtype=np.float32)
        redundancy = np.zeros(len(ids), dtype=np.float32)
        alive = np.ones(len(ids), dtype=bool)

        selected = []
        for _ in range(min(limit, len(ids))):
            value = lam * relevance - (1.0 - lam) * redundancy
            value[~alive] = -np.inf
            idx = int(np.argmax(value))
            if not np.isfinite(value[idx]):
                break
            selected.append(ids[idx])
            alive[idx] = False
            redundancy = np.maximum(redundancy, matrix @ matrix[idx])
        return selected

    # Fallback: pairwise cosine (original behavior).
    selected = []
    remaining = set(ids)
    while remaining and len(selected) < limit:
        best_id, best_val = None, -np.inf
        for cid in remaining:
            relevance = scores[cid]
            redundancy = 0.0
            if selected:
                redundancy = max(
                    (cosine(embeddings.get(cid), embeddings.get(sid)) for sid in selected),
                    default=0.0,
                )
            val = lam * relevance - (1 - lam) * redundancy
            if val > best_val:
                best_id, best_val = cid, val
        if best_id is None:
            break
        selected.append(best_id)
        remaining.remove(best_id)
    return selected


def rank_feed(db, user_id, candidates, limit, *, rated_view=None, compounds=None):
    """Rank candidate MovieCache rows for a user. Returns list of dicts:
    {"movie": row, "score": 0-100 or None, "reason": str, "signals": {...}}.

    Ranking v2 builds bounded evidence components and renormalizes over the
    ones that exist for this user. Collaborative filtering keeps its
    confidence-scaled share; it never silently dominates a sparse profile.
    """
    from services import taste as taste_svc

    if rated_view is None:
        rated_view = taste_svc.load_rated_movies(db, user_id)
    if compounds is None:
        compounds = _review_compound_map(db, user_id)
    taste_vec = taste_svc.build_taste_vector(rated_view, compounds)
    user_signals = features.load_user_signals(
        db, user_id, rated_view=rated_view, compounds=compounds
    )

    # Taste modes: centroids over the positive history so an eclectic viewer
    # is not collapsed into their mediocre middle. Empty for unimodal or
    # short histories — the global vector carries those users alone.
    modes = None
    if enabled("modes"):
        rated_subniches = features.load_subniche_map(
            db, {movie.tmdb_id for movie in rated_view}
        )
        modes = taste_svc.build_taste_modes(
            rated_view, compounds, subniche_map=rated_subniches
        )

    cf_preds, _ = collab.get_predictions(db, user_id)
    use_cf = enabled("cf")
    use_liked = enabled("liked") and user_signals.has_likes and WEIGHTS["liked"] > 0
    use_theme = enabled("theme") and user_signals.has_themes
    use_subniche = enabled("subniche") and user_signals.has_subniches
    use_genre = enabled("genre") and WEIGHTS["genre"] > 0
    use_collection = enabled("collection")

    subniche_map = (
        features.load_subniche_map(db, {movie.tmdb_id for movie in candidates})
        if use_subniche
        else {}
    )

    scored = []
    embeddings = {}
    for movie in candidates:
        emb = decode_embedding(movie.embedding)
        embeddings[movie.tmdb_id] = emb

        components = []
        content_norm = clip_cosine(taste_vec, emb) if taste_vec is not None else None
        # Best matching taste mode can carry the content score alone: a
        # Kurosawa film should not be diluted by this week's comedy phase.
        mode_label = None
        if modes:
            best_mode = None
            best_mode_sim = -1.0
            for mode in modes:
                sim = clip_cosine(mode.vector, emb)
                if sim > best_mode_sim:
                    best_mode, best_mode_sim = mode, sim
            if content_norm is None and best_mode_sim > 0.05:
                content_norm = best_mode_sim
                mode_label = best_mode.label
            elif content_norm is not None and best_mode_sim > content_norm:
                content_norm = best_mode_sim
                mode_label = best_mode.label
        if content_norm is not None:
            components.append((WEIGHTS["content"], content_norm))

        liked_score = None
        if use_liked:
            liked_score = clip_cosine(user_signals.liked_vec, emb)
            components.append((WEIGHTS["liked"], liked_score))

        theme_score = None
        theme_hits = []
        if use_theme:
            theme_score, theme_hits = features.theme_match(
                features.parse_themes(movie.themes), user_signals
            )
            if theme_score is not None:
                components.append((WEIGHTS["theme"], theme_score))

        subniche_score = None
        subniche_hits = []
        if use_subniche:
            subniche_score, subniche_hits = features.subniche_match(
                subniche_map.get(movie.tmdb_id), user_signals
            )
            if subniche_score is not None:
                components.append((WEIGHTS["subniche"], subniche_score))

        genre_score = None
        genre_hits = []
        if use_genre:
            genre_score, genre_hits = features.genre_match(movie.genres, user_signals)
            if genre_score is not None:
                components.append((WEIGHTS["genre"], genre_score))

        collection_score = None
        if use_collection:
            collection_score = features.collection_match(
                movie.collection_id, user_signals
            )
            if collection_score is not None:
                components.append((WEIGHTS["collection"], collection_score))

        quality = _quality_prior(movie)
        components.append((WEIGHTS["quality"], quality))

        total_weight = sum(weight for weight, _ in components)
        base = (
            sum(weight * value for weight, value in components) / total_weight
            if total_weight > 0
            else 0.5 * quality
        )
        if content_norm is None and not use_liked:
            base = 0.85 * base + 0.15 * (
                ((movie.popularity or 0.0) ** 0.15 - 1) / 2.5
            )

        cf = cf_preds.get(movie.tmdb_id) if use_cf else None
        if cf:
            w_cf = W_CF_MAX * cf["confidence"]
            final = (1 - w_cf) * base + w_cf * collab.cf_match_percent(cf["pred"]) / 100.0
        else:
            final = base

        signal_payload = {"quality": round(quality, 4)}
        if mode_label:
            signal_payload["mode"] = mode_label
        if content_norm is not None:
            signal_payload["content"] = round(content_norm, 4)
        if liked_score is not None:
            signal_payload["liked"] = round(liked_score, 4)
        if theme_score is not None:
            signal_payload["theme"] = round(theme_score, 4)
            signal_payload["theme_matches"] = theme_hits[:3]
        if subniche_score is not None:
            signal_payload["subniche"] = round(subniche_score, 4)
            signal_payload["subniche_matches"] = [
                label.split(" · ")[0] for label in subniche_hits[:3]
            ]
        if genre_score is not None:
            signal_payload["genre"] = round(genre_score, 4)
            signal_payload["genre_matches"] = genre_hits[:3]
        if collection_score is not None:
            signal_payload["collection"] = round(collection_score, 4)

        scored.append({
            "movie": movie,
            "raw": final,
            "content": content_norm,
            "mode": mode_label,
            "liked": liked_score,
            "theme": theme_score,
            "theme_hits": theme_hits,
            "subniche": subniche_score,
            "subniche_hits": subniche_hits,
            "genre": genre_score,
            "genre_hits": genre_hits,
            "collection": collection_score,
            "cf": cf,
            "signals": signal_payload,
            "emb": emb,
        })

    if len(scored) <= limit:
        order = [
            s["movie"].tmdb_id
            for s in sorted(scored, key=lambda s: s["raw"], reverse=True)
        ]
    else:
        order = _mmr({s["movie"].tmdb_id: s["raw"] for s in scored}, embeddings, limit)
    by_id = {s["movie"].tmdb_id: s for s in scored}

    results = []
    affinity = (
        taste_svc.genre_affinity(rated_view, compounds, limit=2) if rated_view else None
    )
    loved_genres = [g for g, _ in affinity["loved"]] if affinity else []

    for tmdb_id in order:
        s = by_id[tmdb_id]
        movie = s["movie"]
        reason, signals = _explain(movie, s, loved_genres)
        results.append({
            "movie": movie,
            "score": round(s["raw"] * 100.0, 1),
            "reason": reason,
            "signals": signals,
        })
    return results


def _explain(movie, s, loved_genres):
    """Honest, human-readable reasons derived from the winning signals."""
    signals = dict(s.get("signals") or {})
    cf = s["cf"]
    if cf and cf["confidence"] >= 0.25:
        signals["cf"] = {
            "predicted_rating": cf["pred"],
            "confidence": round(cf["confidence"] * 100),
        }
        return (
            f"Raters with overlapping taste are predicted to give this "
            f"{cf['pred']:.1f}★",
            signals,
        )
    if s.get("collection") is not None and s["collection"] >= 0.65:
        return "Continues a franchise you already rate highly", signals
    if s.get("subniche") is not None and s["subniche"] >= 0.60 and s.get("subniche_hits"):
        niche = s["subniche_hits"][0].split(" · ")[0]
        return f"Squarely in your {niche} niche", signals
    if s.get("mode") and s.get("content") is not None and s["content"] >= 0.55:
        return f"Matches your {s['mode']} side", signals
    if s.get("liked") is not None and s["liked"] >= 0.65:
        return "Close to films you explicitly liked", signals
    if s.get("theme") is not None and s["theme"] >= 0.58 and s.get("theme_hits"):
        return f"Shares your taste for “{s['theme_hits'][0]}”", signals
    if s.get("genre") is not None and s["genre"] >= 0.58 and s.get("genre_hits"):
        return f"Fits your taste for {' and '.join(s['genre_hits'][:2])}", signals
    if loved_genres:
        movie_genres = {g.strip() for g in (movie.genres or "").split(",")}
        hits = [g for g in loved_genres if g in movie_genres]
        if hits:
            return f"Fits your taste for {' and '.join(hits[:2])}", signals
    if s["content"] is not None:
        return "Semantically close to the films you rate highly", signals
    vote = movie.vote_average
    if vote:
        return f"Critically acclaimed ({vote:.1f}/10 on TMDB)", signals
    return "Popular pick to get you started", signals


def cold_start_feed(candidates, limit, seed=None):
    """Deterministic quality-ranked selection used before any ratings exist.

    When ``seed`` is given, near-ties are rotated by a small deterministic
    jitter derived from (seed, tmdb_id) — same seed, same order — so callers
    can vary the discover page without breaking reproducibility.
    """
    def _key(movie):
        base = (movie.vote_average or 0) * np.log1p(movie.popularity or 0)
        if seed is None:
            return base
        digest = zlib.crc32(f"{seed}:{movie.tmdb_id}".encode())
        return base * (1.0 + ((digest % 1000) - 500) / 500.0 * 0.02)

    ranked = sorted(candidates, key=_key, reverse=True)
    return [
        {
            "movie": m,
            "score": None,
            "reason": f"Critically acclaimed ({(m.vote_average or 0):.1f}/10)",
            "signals": {},
        }
        for m in ranked[:limit]
    ]
