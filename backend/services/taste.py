"""Taste profile construction.

The taste vector is built from the user's ratings on the 0.5–5 star scale:

* ratings above the user's midpoint pull the profile toward those movies,
* ratings below it push away (scaled by an aversion factor),
* review sentiment modulates each movie's contribution — a glowing written
  review amplifies the signal, a sour one dampens it (and can flip a marginal
  rating into negative territory).

Because a single centroid collapses an eclectic viewer into a mediocre
middle, :func:`build_taste_modes` additionally splits the positively-rated
history into 2–4 k-means centroids ("taste modes"), silhouette-gated so a
unimodal viewer gets none. Ranking scores content as the best match across
the global vector and the modes.

Also exposes interpretable genre affinities used for explanations.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import os

import numpy as np

from services.embeddings import cosine, decode_embedding

MIDPOINT = 3.0          # neutral point on the 0.5–5 star scale
AVERSION_WEIGHT = 0.35  # how strongly disliked movies push the profile away
SENTIMENT_GAIN = 0.35   # max fractional boost/damp from review sentiment

MODE_MIN_FILMS = 12     # positive ratings needed before modes are trustworthy
MODE_MAX_K = 4          # no human has more than ~4 distinct taste phases
# Silhouette on real embedding clouds sits at 0.03–0.06 (taste clusters
# overlap heavily), so it cannot separate "one taste" from "many"; it only
# rejects degenerate negative fits here. The real gate is distinctness.
MODE_SILHOUETTE_MIN = 0.0
MODE_DISTINCT_MAX = 0.90    # modes must point in genuinely different directions
MODE_MIN_CLUSTER = 4    # a mode needs at least this many member films

DECAY_HALF_LIFE_DAYS = 365.0  # a year-old rating counts half as much
DECAY_FLOOR = 0.35            # but never below ~a third of a fresh rating


def age_multiplier(logged_at):
    """Recency weight for a rating: exponential half-life, floored.

    Old ratings still describe you; they just describe you less. Opt-in via
    ``CINEMATCH_DECAY=1``: the seed cohort carries no per-rating dates (one
    uniform import stamp), and a uniform date column makes decay an exact
    no-op — every weight scales by the same constant, which cancels in all
    normalizations — so the cohort eval cannot measure it. It activates for
    imports that carry real dates (Letterboxd ZIP ``Date`` column).
    """
    if os.getenv("CINEMATCH_DECAY") != "1" or logged_at is None:
        return 1.0
    try:
        age_days = max(
            0.0, (datetime.utcnow() - logged_at).total_seconds() / 86400.0
        )
    except TypeError:
        return 1.0
    return max(DECAY_FLOOR, 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS))


@dataclass
class RatedMovie:
    """Minimal view of a user's rating joined with catalog fields."""

    tmdb_id: int
    rating: float
    embedding: str | None
    genres: str | None
    themes: str | None = None
    collection_id: int | None = None
    release_year: int | None = None
    runtime: int | None = None
    original_language: str | None = None
    logged_at: datetime | None = None


def load_rated_movies(db, user_id) -> list[RatedMovie]:
    """All of a user's ratings joined with their catalog fields."""
    from models.database import LoggedMovie, MovieCache

    rows = (
        db.query(
            LoggedMovie,
            MovieCache.embedding,
            MovieCache.genres,
            MovieCache.themes,
            MovieCache.collection_id,
            MovieCache.release_year,
            MovieCache.runtime,
            MovieCache.original_language,
            LoggedMovie.logged_at,
        )
        .join(MovieCache, LoggedMovie.tmdb_id == MovieCache.tmdb_id)
        .filter(LoggedMovie.user_id == user_id)
        .all()
    )
    return [
        RatedMovie(
            tmdb_id=logged.tmdb_id,
            rating=logged.rating,
            embedding=embedding,
            genres=genres,
            themes=themes,
            collection_id=collection_id,
            release_year=release_year,
            runtime=runtime,
            original_language=original_language,
            logged_at=logged_at,
        )
        for (
            logged,
            embedding,
            genres,
            themes,
            collection_id,
            release_year,
            runtime,
            original_language,
            logged_at,
        ) in rows
    ]


def _sentiment_multiplier(compound: float | None) -> float:
    """Map review compound [-1, 1] to a weight multiplier in [1-gain, 1+gain]."""
    if compound is None:
        return 1.0
    return 1.0 + SENTIMENT_GAIN * max(-1.0, min(1.0, compound))


def _weight_for(rating: float):
    """Centered signed weight; None when the rating is exactly neutral."""
    delta = float(rating) - MIDPOINT
    if abs(delta) < 0.25:
        return None
    return delta


def build_taste_vector(rated_movies, review_compounds=None):
    """Build the unit-norm taste vector.

    rated_movies: iterable of objects with .embedding (JSON str) and .rating.
    review_compounds: optional mapping tmdb_id -> latest review compound.
    Returns np.ndarray or None when there is no usable signal.
    """
    review_compounds = review_compounds or {}
    pos_acc = None
    pos_w = 0.0
    neg_acc = None
    neg_w = 0.0

    for movie in rated_movies:
        vec = decode_embedding(movie.embedding)
        if vec is None:
            continue
        weight = _weight_for(movie.rating)
        if weight is None:
            continue
        weight *= _sentiment_multiplier(review_compounds.get(movie.tmdb_id))
        weight *= age_multiplier(getattr(movie, "logged_at", None))

        if weight > 0:
            pos_acc = vec * weight if pos_acc is None else pos_acc + vec * weight
            pos_w += weight
        else:
            neg_acc = vec * (-weight) if neg_acc is None else neg_acc + vec * (-weight)
            neg_w += -weight

    pos_dir = pos_acc / pos_w if pos_acc is not None and pos_w > 0 else None
    neg_dir = neg_acc / neg_w if neg_acc is not None and neg_w > 0 else None

    if pos_dir is None and neg_dir is None:
        return None
    if pos_dir is None:
        combined = -(AVERSION_WEIGHT / max(neg_w, 1e-6)) * neg_acc
    elif neg_dir is None:
        combined = pos_dir
    else:
        combined = pos_dir - AVERSION_WEIGHT * neg_dir

    norm = np.linalg.norm(combined)
    if norm == 0:
        return None
    return (combined / norm).astype(np.float32)


@dataclass
class TasteMode:
    """One centroid of a multi-mode taste profile.

    A user is not one point: the Kurosawa phase and the dumb-comedy phase
    both deserve their own direction. ``label`` names the mode from member
    subniches (fallback: member genres) so explanations stay honest.
    """

    label: str
    vector: np.ndarray
    size: int
    subniches: tuple[str, ...]
    genres: tuple[str, ...]


def _mode_label(members, subniche_map):
    """Name a mode from its members: dominant subniche, else top genres."""
    subniche_counts: Counter = Counter()
    genre_counts: Counter = Counter()
    for movie in members:
        for label, weight in (subniche_map or {}).get(movie.tmdb_id, []):
            subniche_counts[label.split(" · ")[0]] += weight
        for genre in (movie.genres or "").split(","):
            genre = genre.strip()
            if genre:
                genre_counts[genre] += 1

    if subniche_counts and sum(subniche_counts.values()) >= 0.4 * len(members):
        return subniche_counts.most_common(1)[0][0]
    if genre_counts:
        return ", ".join(genre for genre, _ in genre_counts.most_common(2))
    return "your taste"


def build_taste_modes(
    rated_movies, review_compounds=None, subniche_map=None, *, max_modes=None
):
    """Cluster the user's positively-rated films into taste modes.

    Returns a list of :class:`TasteMode` (possibly empty — an empty list
    means "this history is one taste", and callers fall back to the single
    taste vector). Deterministic: fixed seeds, fixed k search order.

    Only positively-weighted films seed modes (rating above the midpoint,
    sentiment-modulated); aversion stays in the global taste vector.
    ``subniche_map`` is the optional ``{tmdb_id: [(label, weight), ...]}``
    from ``features.load_subniche_map`` used for labeling only.
    """
    max_modes = max_modes or MODE_MAX_K
    members = []
    for movie in rated_movies:
        vec = decode_embedding(movie.embedding)
        weight = _weight_for(movie.rating)
        if vec is None or weight is None:
            continue
        weight *= _sentiment_multiplier((review_compounds or {}).get(movie.tmdb_id))
        weight *= age_multiplier(getattr(movie, "logged_at", None))
        if weight > 0:
            members.append((vec, float(weight), movie))
    if len(members) < MODE_MIN_FILMS:
        return []

    dim = len(members[0][0])
    vectors = [vec for vec, _, _ in members if len(vec) == dim]
    weights = [w for vec, w, _ in members if len(vec) == dim]
    films = [movie for vec, w, movie in members if len(vec) == dim]
    if len(vectors) < MODE_MIN_FILMS:
        return []

    x = np.vstack(vectors).astype(np.float32)
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    best = None  # (labels, silhouette, k)
    for k in range(2, max_modes + 1):
        if len(vectors) < k * MODE_MIN_CLUSTER:
            break
        fit = KMeans(n_clusters=k, n_init=4, random_state=42).fit(x)
        sizes = Counter(fit.labels_.tolist())
        if min(sizes.values()) < MODE_MIN_CLUSTER:
            continue
        # Silhouette alone cannot distinguish "one taste" from "many": any
        # contiguous cloud splits into high-silhouette halves. The real gate
        # is distinctness — the halves of a single-taste continuum are
        # near-identical directions, and that is not two modes.
        centroids = []
        for cluster in sorted(set(fit.labels_.tolist())):
            idx = [i for i, lab in enumerate(fit.labels_.tolist()) if lab == cluster]
            centroid = np.average(
                x[idx], axis=0, weights=[weights[i] for i in idx]
            ).astype(np.float32)
            norm = float(np.linalg.norm(centroid))
            if norm == 0:
                break
            centroids.append(centroid / norm)
        else:
            distinct = True
            for a in range(len(centroids)):
                for b in range(a + 1, len(centroids)):
                    if cosine(centroids[a], centroids[b]) > MODE_DISTINCT_MAX:
                        distinct = False
                        break
                if not distinct:
                    break
            if not distinct:
                continue
        sil = float(silhouette_score(x, fit.labels_, metric="cosine"))
        if sil < MODE_SILHOUETTE_MIN:
            continue
        if best is None or sil > best[1]:
            best = (fit.labels_, sil, k)
    if best is None:
        return []
    labels, _sil, _k = best

    modes = []
    for cluster in sorted(set(labels.tolist())):
        idx = [i for i, lab in enumerate(labels.tolist()) if lab == cluster]
        centroid = np.average(
            np.vstack([vectors[i] for i in idx]),
            axis=0,
            weights=[weights[i] for i in idx],
        ).astype(np.float32)
        norm = float(np.linalg.norm(centroid))
        if norm == 0:
            continue
        mode_members = [films[i] for i in idx]
        subniche_hits = Counter()
        for movie in mode_members:
            for label, weight in (subniche_map or {}).get(movie.tmdb_id, []):
                subniche_hits[label.split(" · ")[0]] += weight
        genre_hits = Counter()
        for movie in mode_members:
            for genre in (movie.genres or "").split(","):
                genre = genre.strip()
                if genre:
                    genre_hits[genre] += 1
        modes.append(
            TasteMode(
                label=_mode_label(mode_members, subniche_map),
                vector=centroid / norm,
                size=len(idx),
                subniches=tuple(label for label, _ in subniche_hits.most_common(3)),
                genres=tuple(genre for genre, _ in genre_hits.most_common(3)),
            )
        )
    return modes


def genre_affinity(rated_movies, review_compounds=None, limit=5):
    """Interpretable genre weights from liked vs disliked history."""
    review_compounds = review_compounds or {}
    scores = {}
    for movie in rated_movies:
        weight = _weight_for(movie.rating)
        if weight is None or not movie.genres:
            continue
        weight *= _sentiment_multiplier(review_compounds.get(movie.tmdb_id))
        weight *= age_multiplier(getattr(movie, "logged_at", None))
        for genre in movie.genres.split(","):
            genre = genre.strip()
            if genre:
                scores[genre] = scores.get(genre, 0.0) + weight
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    loved = [(g, round(w, 2)) for g, w in ranked[:limit] if w > 0]
    avoided = [(g, round(w, 2)) for g, w in reversed(ranked[-limit:]) if w < 0]
    return {"loved": loved, "avoided": avoided}


def match_percent(similarity: float) -> float:
    """Cosine similarity [-1, 1] → friendly 0–100 match percentage."""
    return round(max(0.0, min(100.0, (similarity + 1.0) / 2.0 * 100.0)), 1)


def score_by_taste(taste_vector, candidates):
    """Score candidates purely by embedding proximity to the taste vector.

    candidates: iterable of MovieCache rows. Returns list of
    (movie, similarity) unsorted.
    """
    out = []
    for movie in candidates:
        vec = decode_embedding(movie.embedding)
        out.append((movie, cosine(taste_vector, vec)))
    return out


def summarize(rated_movies, review_compounds=None):
    """Plain-language description of the profile for UI display."""
    affinity = genre_affinity(rated_movies, review_compounds, limit=3)
    loved = [g for g, _ in affinity["loved"]]
    avoided = [g for g, _ in affinity["avoided"]]
    n = len(rated_movies)

    if n == 0:
        return "Log and rate a few films and your taste profile will appear here."
    parts = []
    if loved:
        parts.append(f"drawn to {' and '.join(loved)}")
    if avoided:
        parts.append(f"cool on {' and '.join(avoided)}")
    body = ", ".join(parts) if parts else "still exploring"
    return f"Based on {n} rated film{'s' if n != 1 else ''}: {body}."
