"""Ranking v2 configuration.

Weights are explicit so evaluation ablations can disable one signal without
editing code. Missing signals are removed from the blend and the remaining
weights are renormalized, so a user without likes is not penalized for it.

Set ``CINEMATCH_DISABLE=liked,theme,genre,collection,subniche,modes,cf`` to
reproduce an ablation. The default is all signals enabled.
"""

from __future__ import annotations

import os

RANKER_VERSION = "v2"

WEIGHTS = {
    "content": 0.48,
    # The direct liked-centroid score was tested and hurt NDCG; likes still
    # feed theme/genre/collection affinity below and still suppress already-
    # consumed films from candidates. Keep the slot at zero for transparency.
    "liked": 0.0,
    "theme": 0.12,
    # Genre was useful while only part of the catalog had themes. With full
    # theme coverage it double-counts the same metadata and a full-coverage
    # ablation showed that removing it raises NDCG@20 from 0.2266 to 0.2453.
    "genre": 0.0,
    "collection": 0.04,
    # Derived subniches (NMF over Letterboxd theme co-occurrence) are more
    # specific than raw themes; weight them below theme until the ablation
    # proves they earn more.
    "subniche": 0.10,
    "quality": 0.16,
}

W_CF_MAX = 0.35
MMR_LAMBDA = 0.70

_DISABLED = {
    item.strip().lower()
    for item in (os.getenv("CINEMATCH_DISABLE") or "").split(",")
    if item.strip()
}


def enabled(signal: str) -> bool:
    return signal not in _DISABLED


def describe() -> dict:
    return {
        "version": RANKER_VERSION,
        "weights": {
            name: weight
            for name, weight in WEIGHTS.items()
            if name == "content" or enabled(name)
        },
        "disabled": sorted(_DISABLED),
        "cf_max": W_CF_MAX,
        "mmr_lambda": MMR_LAMBDA,
    }
