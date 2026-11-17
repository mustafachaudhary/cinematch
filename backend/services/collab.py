"""Item-item collaborative filtering.

Neighborhood CF over the user×film rating matrix. Similarities are computed
on residuals after removing global, user and shrunken item-bias baselines;
cosine similarity is shrunk toward zero when co-rating evidence is thin.
Predictions degrade gracefully: with no co-rating data the module reports
zero confidence and the recommender leans on content signals.

The derived artifacts (residual matrix, item similarities, per-user
predictions) are cached process-wide and invalidated by a cheap fingerprint
of the ratings table, because rebuilding them per request dominated the
feed and search latency.
"""

import threading
from collections import defaultdict

import numpy as np

SHRINKAGE = 8.0   # co-rating count at which similarity is trusted ~50%
MAX_NEIGHBORS = 20
CONFIDENCE_CAP = 3.0  # Σ|sim| needed for full confidence
ITEM_BIAS_SHRINKAGE = 3.0  # co-ratings at which an item bias is half trusted


def load_rating_matrix(db):
    """Load the ratings table as {user_id: {film_id: rating}}."""
    from models.database import Rating

    matrix = defaultdict(dict)
    for row in db.query(Rating.user_id, Rating.film_id, Rating.rating).all():
        matrix[row[0]][row[1]] = float(row[2])
    return dict(matrix)


def _center(matrix):
    """Mean-center each user's ratings. Returns ({user: mean}, centered copy)."""
    means = {}
    centered = {}
    for user, items in matrix.items():
        if not items:
            continue
        mean = sum(items.values()) / len(items)
        means[user] = mean
        centered[user] = {film: r - mean for film, r in items.items()}
    return means, centered


def _baseline_stats(matrix):
    """Global mean, user offsets and shrunken item biases for baseline CF.

    The original user-mean-centred model treats a 4.3-star film in a user's
    history as "average" for that user even when the film is broadly loved.
    Subtracting a shrunken item bias first leaves a cleaner residual for the
    item-item neighbours and lowers holdout error, especially for users with
    few ratings.
    """
    total = 0.0
    count = 0
    item_sums = defaultdict(float)
    item_counts = defaultdict(int)
    user_means = {}
    for user, items in matrix.items():
        if not items:
            continue
        user_means[user] = sum(items.values()) / len(items)
        for film, rating in items.items():
            total += rating
            count += 1
            item_sums[film] += rating
            item_counts[film] += 1
    global_mean = total / count if count else 3.0
    item_bias = {
        film: (item_sums[film] / item_counts[film] - global_mean)
        * item_counts[film]
        / (item_counts[film] + ITEM_BIAS_SHRINKAGE)
        for film in item_sums
    }
    return {
        "global_mean": float(global_mean),
        "user_mean": user_means,
        "item_bias": item_bias,
    }


def _baseline_residuals(matrix, baselines):
    """Residuals after removing global, user and item baseline effects."""
    global_mean = float(baselines["global_mean"])
    user_means = baselines["user_mean"]
    item_bias = baselines["item_bias"]
    residuals = {}
    for user, items in matrix.items():
        if not items:
            continue
        user_baseline = global_mean + (user_means.get(user, global_mean) - global_mean)
        residuals[user] = {
            film: rating - (user_baseline + item_bias.get(film, 0.0))
            for film, rating in items.items()
        }
    return residuals


MAX_SIMILAR_NEIGHBORS = 50
DENSE_FULL_LIMIT = 2500
SIM_CHUNK_SIZE = 1024


def item_similarity(centered, *, max_neighbors=MAX_SIMILAR_NEIGHBORS,
                    full_limit=DENSE_FULL_LIMIT):
    """Pairwise item-item cosine, shrunk by co-rating support.

    ``sim(i,j) = cosine(centered_i, centered_j) * n_ij / (n_ij + SHRINKAGE)``
    over users who rated both films (``n_ij >= 2``), positive-only.

    The old pairwise Python loop is quadratic in films and rebuilt millions of
    ``set`` objects. The cohort import grows the catalog to ~10k films, so this
    version materializes the user-dimension matrix (small: one column per
    user), computes cosine in NumPy, and emits only the top neighbors per film.
    Small matrices keep the exact all-pairs behavior used by tests.

    Returns ``{film_i: {film_j: sim}}`` (symmetric, positive-only).
    """
    if not centered:
        return {}

    films = sorted({film for items in centered.values() for film in items})
    users = sorted(centered)
    if not films or not users:
        return {}
    film_index = {film: index for index, film in enumerate(films)}
    user_index = {user: index for index, user in enumerate(users)}

    x = np.zeros((len(films), len(users)), dtype=np.float32)
    binary = np.zeros_like(x)
    for user, items in centered.items():
        u = user_index[user]
        for film, rating in items.items():
            i = film_index[film]
            x[i, u] = rating
            binary[i, u] = 1.0

    norms = np.linalg.norm(x, axis=1)
    inv_norms = np.divide(
        1.0, norms, out=np.zeros_like(norms), where=norms > 0
    )
    normalized = x * inv_norms[:, None]

    # For small matrices emit every positive edge; for the production-sized
    # catalog cap each film's neighbor list. Predictions only consult the top
    # MAX_NEIGHBORS anyway, so 50 is a comfortable margin.
    top_k = len(films) if len(films) <= full_limit else max(1, max_neighbors)
    sims = {}
    for start in range(0, len(films), SIM_CHUNK_SIZE):
        end = min(start + SIM_CHUNK_SIZE, len(films))
        cosine = normalized[start:end] @ normalized.T
        co_counts = binary[start:end] @ binary.T

        for local_index, global_index in enumerate(range(start, end)):
            co = co_counts[local_index]
            sim_row = cosine[local_index]
            mask = (co >= 2) & (sim_row > 0)
            mask[global_index] = False
            if not mask.any():
                continue

            weighted = np.where(
                mask,
                sim_row * (co / (co + SHRINKAGE)),
                0.0,
            )
            positive = int(mask.sum())
            if positive > top_k:
                chosen = np.argpartition(weighted, -top_k)[-top_k:]
            else:
                chosen = np.flatnonzero(mask)
            chosen = chosen[weighted[chosen] > 0]
            if not chosen.size:
                continue
            # Deterministic strongest-first order; dicts preserve insertion
            # order, which keeps engine-status output stable.
            chosen = chosen[np.argsort(-weighted[chosen])]

            film_a = films[global_index]
            row = {}
            for neighbor in chosen:
                film_b = films[int(neighbor)]
                similarity = float(weighted[neighbor])
                row[film_b] = similarity
                sims.setdefault(film_b, {})[film_a] = similarity
            sims.setdefault(film_a, {}).update(row)
    return sims


def similar_films(film_id, sims, limit=10):
    """Nearest films to `film_id` under CF similarity."""
    pairs = sorted(sims.get(film_id, {}).items(), key=lambda kv: kv[1], reverse=True)
    return [(film, round(sim, 4)) for film, sim in pairs[:limit]]


def predict_for_user(user_ratings, sims, *, baselines=None, user_id=None):
    """CF predictions for one user.

    user_ratings: {film_id: rating} — this user's explicit ratings.
    Returns {candidate_film: {"pred": rating_0_5_to_5, "confidence": 0_1}}.

    When ``baselines`` is supplied the similarities are expected to have been
    built from :func:`_baseline_residuals`; otherwise the classic user-mean
    centred path is retained for direct callers and tests.
    """
    if not user_ratings or not sims:
        return {}

    item_bias = baselines.get("item_bias", {}) if baselines else {}
    if baselines:
        global_mean = float(baselines["global_mean"])
        user_mean = baselines.get("user_mean", {}).get(
            user_id, sum(user_ratings.values()) / len(user_ratings)
        )
        mean = global_mean + (user_mean - global_mean)
    else:
        mean = sum(user_ratings.values()) / len(user_ratings)
    rated_ids = list(user_ratings)

    predictions = {}
    candidates = set()
    for film in rated_ids:
        candidates.update(sims.get(film, {}))
    candidates -= set(rated_ids)

    for candidate in candidates:
        num = 0.0
        den = 0.0
        neighbors = []
        for film in rated_ids:
            sim = sims.get(candidate, {}).get(film)
            if sim:
                neighbors.append((sim, film, user_ratings[film]))
        neighbors.sort(key=lambda t: t[0], reverse=True)
        for sim, film, rating in neighbors[:MAX_NEIGHBORS]:
            residual = rating - mean - item_bias.get(film, 0.0)
            num += sim * residual
            den += abs(sim)
        if den <= 1e-6:
            continue
        pred_centered = num / den
        candidate_baseline = mean + item_bias.get(candidate, 0.0)
        pred = max(0.5, min(5.0, candidate_baseline + pred_centered))
        confidence = min(1.0, den / CONFIDENCE_CAP)
        predictions[candidate] = {
            "pred": round(float(pred), 3),
            "confidence": round(float(confidence), 3),
        }
    return predictions


def cf_match_percent(pred_rating: float) -> float:
    """Predicted star rating [0.5, 5] → 0–100 match score."""
    return round(max(0.0, min(100.0, ((pred_rating - 0.5) / 4.5) * 100.0)), 1)


# ---------------------------------------------------------------------------
# Cached derived artifacts
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_model_cache = {"version": None, "matrix": None, "sims": None, "baselines": None}
_pred_lock = threading.Lock()
_pred_cache = {"key": None, "preds": None}


def matrix_version(db):
    """Cheap fingerprint of the ratings table for cache invalidation.

    Row count, latest timestamp and rating sum change on every insert,
    update or delete — everything the derived caches need to know."""
    from sqlalchemy import func

    from models.database import Rating

    count, latest, total = (
        db.query(
            func.count(Rating.film_id),
            func.max(Rating.rated_at),
            func.sum(Rating.rating),
        ).one()
    )
    return (int(count or 0), str(latest or ""), round(float(total or 0.0), 4))


def get_model(db):
    """Return (matrix, sims, version), rebuilding only when ratings changed.

    ``sims`` is the thin-shrunk item-item similarity map used by the feed,
    search and the engine-status endpoint — previously rebuilt from scratch
    on every one of those calls."""
    version = matrix_version(db)
    with _model_lock:
        if _model_cache["version"] == version and _model_cache["matrix"] is not None:
            return _model_cache["matrix"], _model_cache["sims"], version

    matrix = load_rating_matrix(db)
    baselines = _baseline_stats(matrix) if len(matrix) >= 2 else None
    residuals = _baseline_residuals(matrix, baselines) if baselines else {}
    sims = item_similarity(residuals) if baselines else {}

    with _model_lock:
        _model_cache.update(
            version=version, matrix=matrix, sims=sims, baselines=baselines
        )
    return matrix, sims, version


def get_predictions(db, user_id):
    """Return (predictions, sims) for a user, cached until the matrix changes."""
    matrix, sims, version = get_model(db)
    key = (user_id, version)
    with _pred_lock:
        if _pred_cache["key"] == key and _pred_cache["preds"] is not None:
            return _pred_cache["preds"], sims

    preds = predict_for_user(
        matrix.get(user_id, {}),
        sims,
        baselines=_model_cache.get("baselines"),
        user_id=user_id,
    )

    with _pred_lock:
        _pred_cache.update(key=key, preds=preds)
    return preds, sims


def cf_state(db):
    """Quick health snapshot of the CF matrix (for /movies/engine-status)."""
    matrix, sims, _ = get_model(db)
    n_users = len(matrix)
    n_ratings = sum(len(items) for items in matrix.values())
    n_similarities = sum(len(row) for row in sims.values()) // 2
    return {
        "users_with_ratings": n_users,
        "total_ratings": n_ratings,
        "item_similarity_pairs": n_similarities,
        "cf_viable": n_users >= 2 and n_similarities > 0,
    }
