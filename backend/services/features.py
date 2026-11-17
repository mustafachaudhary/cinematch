"""User-level ranking signals derived from ratings, likes and film metadata.

This module deliberately keeps every signal bounded and support-gated:

* theme and genre affinities are normalized to [-1, 1] and only used when the
  user has enough evidence (5 themed films / 3 ratings per genre);
* likes are a separate positive-only centroid, never folded into star ratings;
* collection affinity is a small franchise bonus, not a hidden rating.

All functions are pure/read-only and can be ablated from ranking v2 through
``CINEMATCH_DISABLE``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from services.embeddings import decode_embedding
from services.taste import MIDPOINT, _sentiment_multiplier, age_multiplier

THEME_GATE = 5
GENRE_GATE = 3
SUBNICHE_GATE = 3  # derived subniches are specific; fewer rated films needed
SUBNICHE_MIN_WEIGHT = 0.15  # faint NMF memberships carry no reliable signal
LIKED_THEME_WEIGHT = 0.8
LIKED_GENRE_WEIGHT = 0.5
LIKED_COLLECTION_WEIGHT = 0.8
LIKED_SUBNICHE_WEIGHT = 0.8


@dataclass
class UserSignals:
    theme_affinity: dict[str, float] = field(default_factory=dict)
    theme_support: int = 0
    subniche_affinity: dict[str, float] = field(default_factory=dict)
    subniche_support: int = 0
    genre_affinity: dict[str, float] = field(default_factory=dict)
    genre_support: dict[str, int] = field(default_factory=dict)
    collection_affinity: dict[int, float] = field(default_factory=dict)
    liked_vec: np.ndarray | None = None
    liked_ids: set[int] = field(default_factory=set)
    liked_count: int = 0

    @property
    def has_likes(self) -> bool:
        return self.liked_vec is not None

    @property
    def has_themes(self) -> bool:
        return self.theme_support >= THEME_GATE and bool(self.theme_affinity)

    @property
    def has_subniches(self) -> bool:
        return self.subniche_support >= SUBNICHE_GATE and bool(self.subniche_affinity)


@lru_cache(maxsize=30000)
def parse_themes(raw: str | None) -> tuple[dict, ...]:
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(payload, list):
        return ()
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        if not label:
            continue
        out.append(
            {
                "id": item.get("id") or label.casefold(),
                "label": label,
                "weight": float(item.get("weight") or 1.0),
                "source": item.get("source") or "unknown",
            }
        )
    return tuple(out)


@lru_cache(maxsize=30000)
def parse_keywords(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(str(item).strip() for item in payload if str(item).strip())


def _normalize(scores: dict) -> dict:
    if not scores:
        return {}
    peak = max((abs(value) for value in scores.values()), default=0.0)
    if peak <= 0:
        return {}
    return {key: max(-1.0, min(1.0, float(value) / peak)) for key, value in scores.items()}


@lru_cache(maxsize=1)
def _subniche_lifts() -> dict[str, float]:
    """Measured per-cluster lift from the cohort (scripts/measure_label_lift.py).

    Subniches the cohort rates below base rate (comedy, for this cohort)
    should not push candidates as hard as ones it loves. The artifact is
    optional: absent or unreadable means multiplier 1.0 everywhere and the
    signal falls back to raw affinity. Path overridable via CINEMATCH_LABEL_LIFT.
    """
    path = os.getenv(
        "CINEMATCH_LABEL_LIFT",
        str(Path(__file__).resolve().parents[1] / "reports" / "label_lift.json"),
    )
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        row["id"]: float(row["lift"])
        for row in data.get("subniches", {}).get("measured", [])
        if row.get("lift")
    }


def _subniche_lift_multiplier(label: str) -> float:
    """Cohort-trust multiplier in [0.5, 1.5]; 1.0 when lift is unmeasured."""
    lift = _subniche_lifts().get(label)
    if lift is None:
        return 1.0
    return max(0.5, min(1.5, lift))


@lru_cache(maxsize=1)
def _theme_lifts() -> dict[str, float]:
    """Cohort-trust weights for theme labels — opt-in, off by default.

    The 2026-11-09 gate run (reports/eval_theme_lift_gate.md) found that
    applying cohort lift to the 207 theme labels REGRESSED warm NDCG@20 by
    2.28% relative (paired t = −2.16, the only statistically significant
    delta measured that day). Fine-grained label lifts rest on thin support
    (often n < 100) and the per-user THEME_GATE already filters label trust
    individually; cohort-level reweighting double-counts noisy evidence.
    Coarse subniche clusters (32, support ≥ 1,900) have stable lifts and keep
    their weighting. Set CINEMATCH_THEME_LIFT=1 to experiment.
    """
    if os.getenv("CINEMATCH_THEME_LIFT") != "1":
        return {}
    path = os.getenv(
        "CINEMATCH_LABEL_LIFT",
        str(Path(__file__).resolve().parents[1] / "reports" / "label_lift.json"),
    )
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        row["id"]: float(row["lift"])
        for row in data.get("themes", {}).get("measured", [])
        if row.get("lift")
    }


def _theme_lift_multiplier(theme_id: str) -> float:
    """Cohort-trust multiplier in [0.5, 1.5]; 1.0 when lift is unmeasured."""
    lift = _theme_lifts().get(theme_id)
    if lift is None:
        return 1.0
    return max(0.5, min(1.5, lift))


def load_subniche_map(db, tmdb_ids) -> dict[int, list[tuple[str, float]]]:
    """Weighted subniche memberships for a set of films.

    Returns ``{tmdb_id: [(cluster_label, membership_weight), ...]}`` sorted by
    weight. Chunked because candidate lists can span the whole catalog.
    """
    from models.database import FilmSubniche, ThemeCluster

    ids = [int(tmdb_id) for tmdb_id in set(tmdb_ids or [])]
    if not ids:
        return {}
    out: dict[int, list[tuple[str, float]]] = {}
    for start in range(0, len(ids), 900):
        rows = (
            db.query(FilmSubniche.tmdb_id, FilmSubniche.weight, ThemeCluster.label)
            .join(ThemeCluster, ThemeCluster.id == FilmSubniche.cluster_id)
            .filter(FilmSubniche.tmdb_id.in_(ids[start : start + 900]))
            .all()
        )
        for tmdb_id, weight, label in rows:
            if weight is None or float(weight) < SUBNICHE_MIN_WEIGHT:
                continue
            out.setdefault(int(tmdb_id), []).append((label or "", float(weight)))
    for memberships in out.values():
        memberships.sort(key=lambda item: item[1], reverse=True)
    return out


def load_user_signals(db, user_id: int, rated_view=None, compounds=None) -> UserSignals:
    """Build likes/themes/genres/collection/subniche affinities for one user."""
    from models.database import MovieCache, UserLike

    if rated_view is None:
        from services import taste as taste_svc

        rated_view = taste_svc.load_rated_movies(db, user_id)
    compounds = compounds or {}

    theme_scores: dict[str, float] = {}
    subniche_scores: dict[str, float] = {}
    genre_scores: dict[str, float] = {}
    genre_support: dict[str, int] = {}
    collection_scores: dict[int, float] = {}
    theme_support = 0
    subniche_support = 0

    subniche_map = load_subniche_map(db, {movie.tmdb_id for movie in rated_view})

    for movie in rated_view:
        weight = float(movie.rating or 0.0) - MIDPOINT
        if abs(weight) < 0.25:
            continue
        weight *= _sentiment_multiplier(compounds.get(movie.tmdb_id))
        weight *= age_multiplier(getattr(movie, "logged_at", None))
        themes = parse_themes(getattr(movie, "themes", None))
        if themes:
            theme_support += 1
            for theme in themes:
                theme_scores[theme["id"]] = theme_scores.get(theme["id"], 0.0) + (
                    weight * theme["weight"] * _theme_lift_multiplier(theme["id"])
                )
        memberships = subniche_map.get(movie.tmdb_id)
        if memberships:
            subniche_support += 1
            for label, membership_weight in memberships:
                subniche_scores[label] = subniche_scores.get(label, 0.0) + (
                    weight * membership_weight * _subniche_lift_multiplier(label)
                )
        if movie.genres:
            for genre in movie.genres.split(","):
                genre = genre.strip()
                if not genre:
                    continue
                genre_scores[genre] = genre_scores.get(genre, 0.0) + weight
                genre_support[genre] = genre_support.get(genre, 0) + 1
        collection_id = getattr(movie, "collection_id", None)
        if collection_id:
            key = int(collection_id)
            collection_scores[key] = collection_scores.get(key, 0.0) + weight

    liked_rows = (
        db.query(UserLike.tmdb_id, UserLike.liked_at, MovieCache.embedding,
                 MovieCache.themes, MovieCache.genres, MovieCache.collection_id)
        .join(MovieCache, MovieCache.tmdb_id == UserLike.tmdb_id)
        .filter(UserLike.user_id == user_id)
        .all()
    )
    liked_ages = {}
    liked_acc = None
    liked_ids = set()
    for tmdb_id, liked_at, embedding, themes_raw, genres, collection_id in liked_rows:
        liked_ids.add(int(tmdb_id))
        liked_ages[int(tmdb_id)] = liked_at
        vec = decode_embedding(embedding)
        if vec is not None:
            liked_acc = vec.copy() if liked_acc is None else liked_acc + vec
        themes = parse_themes(themes_raw)
        if themes:
            theme_support += 1
            for theme in themes:
                theme_scores[theme["id"]] = theme_scores.get(theme["id"], 0.0) + (
                    LIKED_THEME_WEIGHT
                    * theme["weight"]
                    * _theme_lift_multiplier(theme["id"])
                )
        if genres:
            for genre in genres.split(","):
                genre = genre.strip()
                if not genre:
                    continue
                genre_scores[genre] = genre_scores.get(genre, 0.0) + (
                    LIKED_GENRE_WEIGHT * age_multiplier(liked_at)
                )
                genre_support[genre] = genre_support.get(genre, 0) + 1
        if collection_id:
            key = int(collection_id)
            collection_scores[key] = collection_scores.get(key, 0.0) + (
                LIKED_COLLECTION_WEIGHT * age_multiplier(liked_at)
            )

    liked_subniches = load_subniche_map(db, liked_ids)
    for tmdb_id in liked_ids:
        liked_at = liked_ages.get(int(tmdb_id))
        like_age = age_multiplier(liked_at)
        for label, membership_weight in liked_subniches.get(int(tmdb_id), []):
            subniche_scores[label] = subniche_scores.get(label, 0.0) + (
                LIKED_SUBNICHE_WEIGHT
                * membership_weight
                * _subniche_lift_multiplier(label)
                * like_age
            )
        if liked_subniches.get(int(tmdb_id)):
            subniche_support += 1

    liked_vec = None
    if liked_acc is not None:
        norm = float(np.linalg.norm(liked_acc))
        if norm > 0:
            liked_vec = (liked_acc / norm).astype(np.float32)

    return UserSignals(
        theme_affinity=_normalize(theme_scores),
        theme_support=theme_support,
        subniche_affinity=_normalize(subniche_scores),
        subniche_support=subniche_support,
        genre_affinity=_normalize(genre_scores),
        genre_support=genre_support,
        collection_affinity=_normalize(collection_scores),
        liked_vec=liked_vec,
        liked_ids=liked_ids,
        liked_count=len(liked_ids),
    )


def theme_match(movie_themes, signals: UserSignals):
    """Return (score in [0,1], [matching labels]) or (None, [])."""
    if not signals.has_themes or not movie_themes:
        return None, []
    total = 0.0
    accumulated = 0.0
    hits = []
    for theme in movie_themes:
        weight = theme.get("weight", 1.0)
        total += weight
        affinity = signals.theme_affinity.get(theme["id"], 0.0)
        if affinity:
            accumulated += affinity * weight
            hits.append((theme["label"], affinity))
    if total <= 0 or not hits:
        return None, []
    average = accumulated / total
    hits.sort(key=lambda item: abs(item[1]), reverse=True)
    return max(0.0, min(1.0, 0.5 + 0.5 * average)), [label for label, _ in hits]


def subniche_match(movie_subniches, signals: UserSignals):
    """Return (score in [0,1], [matching labels]) or (None, []).

    ``movie_subniches`` is the film's weighted membership list —
    ``[(cluster_label, weight), ...]`` from :func:`load_subniche_map`. The
    film's membership weights partition its identity across subniches, so the
    affinity is a weighted average over the subniches it actually belongs to,
    not over a flat label list.
    """
    if not signals.has_subniches or not movie_subniches:
        return None, []
    total = 0.0
    accumulated = 0.0
    hits = []
    for label, weight in movie_subniches:
        if not label:
            continue
        total += weight
        affinity = signals.subniche_affinity.get(label, 0.0)
        if affinity:
            accumulated += affinity * weight
            hits.append((label, affinity))
    if total <= 0 or not hits:
        return None, []
    average = accumulated / total
    hits.sort(key=lambda item: abs(item[1]), reverse=True)
    return max(0.0, min(1.0, 0.5 + 0.5 * average)), [label for label, _ in hits]


def genre_match(movie_genres, signals: UserSignals):
    """Return (score in [0,1], [known genres]) or (None, [])."""
    if not movie_genres:
        return None, []
    genres = [genre.strip() for genre in movie_genres.split(",") if genre.strip()]
    known = [
        genre
        for genre in genres
        if signals.genre_support.get(genre, 0) >= GENRE_GATE
    ]
    if not known:
        return None, []
    average = sum(signals.genre_affinity.get(genre, 0.0) for genre in known) / len(known)
    return max(0.0, min(1.0, 0.5 + 0.5 * average)), known


def collection_match(collection_id, signals: UserSignals):
    """Return score in [0,1] when the user already rates this franchise."""
    if not collection_id:
        return None
    value = signals.collection_affinity.get(int(collection_id))
    if value is None:
        return None
    return max(0.0, min(1.0, 0.5 + 0.5 * math.tanh(value)))
