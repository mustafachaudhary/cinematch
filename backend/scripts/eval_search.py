"""Offline evaluation for CineMatch search.

Search never had a gate; this gives it two suites on the same deterministic
20% user-level holdout the feed eval uses:

* **mood** — the gate for personalization. For each of the eight mood
  profiles, the canonical trigger phrase is searched and relevance is defined
  as held-out films the persona rated above their threshold whose genres
  intersect the mood's genre profile. This measures how well the taste nudge
  lifts films the user actually loved *within* a mood, not mood detection.
* **anchor** — a baseline for "movies like X" retrieval. Anchors are held-out
  highly-rated films; relevance is the persona's other held-out highly-rated
  films. The anchor branch uses behavior/CF evidence, not taste vectors, so
  it is arm-independent and reported once.

Both arms (global taste vector vs max-over-modes nudge) run in one process
against one split, and per-persona paired deltas with a paired t are reported
for the mood suite.

Usage:
    cd backend && ../.venv/bin/python scripts/eval_search.py \
        --db cinematch.db --out reports/eval_search.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

MOOD_QUERIES = {
    "cry": "something to make me cry",
    "cheer_up": "something uplifting and warm",
    "funny": "something funny",
    "romantic": "something romantic",
    "mind_bending": "something mind-bending",
    "epic_adventure": "an epic adventure",
    "nostalgic": "something nostalgic",
    "dark_atmospheric": "something dark and atmospheric",
}


def _gain(rating: float) -> float:
    if rating is None or rating <= 0:
        return 0.0
    return float(2.0 ** (float(rating) - 0.5) - 1.0)


def _ndcg_at(recommended: list[int], ratings: dict[int, float], k: int) -> float | None:
    if not ratings:
        return None
    dcg = sum(
        _gain(ratings.get(tmdb_id, 0.0)) / math.log2(i + 2)
        for i, tmdb_id in enumerate(recommended[:k])
    )
    ideal = sorted((_gain(v) for v in ratings.values()), reverse=True)[:k]
    ideal_dcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / ideal_dcg if ideal_dcg > 0 else None


def _recall_at(recommended: list[int], relevant: set[int], k: int) -> float | None:
    if not relevant:
        return None
    return len(set(recommended[:k]) & relevant) / len(relevant)


def _mrr(recommended: list[int], relevant: set[int]) -> float:
    for i, tmdb_id in enumerate(recommended, 1):
        if tmdb_id in relevant:
            return 1.0 / i
    return 0.0


def _macro(rows: list[dict]) -> dict:
    out = {}
    for key in ("ndcg20", "recall10", "recall20", "mrr"):
        values = [row[key] for row in rows if row.get(key) is not None]
        out[key] = sum(values) / len(values) if values else None
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--eval-db", type=Path, default=Path("/tmp/opencode/cinematch_eval/eval_search.db"))
    parser.add_argument("--out", type=Path, default=BACKEND / "reports" / "eval_search.json")
    parser.add_argument("--test-frac", type=float, default=0.2)
    parser.add_argument("--min-test", type=int, default=20)
    parser.add_argument("--max-test", type=int, default=200)
    parser.add_argument("--min-ratings", type=int, default=100)
    parser.add_argument("--anchors-per-persona", type=int, default=8)
    parser.add_argument("--allow-missing-embeddings", action="store_true")
    args = parser.parse_args()

    source = args.db.resolve()
    if not source.exists():
        print(f"db does not exist: {source}", file=sys.stderr)
        return 2

    started = time.monotonic()
    from eval_recommender import prepare_eval_db

    personas = prepare_eval_db(
        source,
        args.eval_db,
        test_frac=args.test_frac,
        min_test=args.min_test,
        max_test=args.max_test,
        min_ratings=args.min_ratings,
        include_mustafa=True,
    )
    if not personas:
        print("no evaluation personas found", file=sys.stderr)
        return 2

    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{args.eval_db}"
    import sqlite3 as _sqlite3

    from models.database import MovieCache, Review, SessionLocal
    from services import collab, ranking, search as search_svc, taste
    from services.search import MOOD_PROFILES

    db = SessionLocal()
    try:
        catalog = db.query(MovieCache).all()
        by_id = {movie.tmdb_id: movie for movie in catalog}

        holdout: dict[int, dict[int, float]] = defaultdict(dict)
        con = _sqlite3.connect(f"file:{args.eval_db}?mode=ro", uri=True)
        try:
            for user_id, tmdb_id, rating in con.execute(
                "select user_id, tmdb_id, rating from eval_holdout"
            ):
                holdout[int(user_id)][int(tmdb_id)] = float(rating)
        finally:
            con.close()

        mood_rows = {"global": [], "modes": []}
        anchor_rows = []
        personas_with_modes = 0

        for index, persona in enumerate(personas, 1):
            user_id = persona["user_id"]
            user_holdout = holdout.get(user_id, {})
            if not user_holdout:
                continue

            compounds = {
                int(row[0]): float(row[1] or 0.0)
                for row in db.query(Review.tmdb_id, Review.sentiment_compound)
                .filter(Review.user_id == user_id)
                .all()
            }
            rated_view = taste.load_rated_movies(db, user_id)
            taste_vec = taste.build_taste_vector(rated_view, compounds)
            modes = None
            if ranking.enabled("modes"):
                from services import features as features_svc

                sub_map = features_svc.load_subniche_map(
                    db, {movie.tmdb_id for movie in rated_view}
                )
                modes = taste.build_taste_modes(
                    rated_view, compounds, subniche_map=sub_map
                )
            if modes:
                personas_with_modes += 1

            threshold = float(persona["threshold"])
            loved = {
                tmdb_id: rating
                for tmdb_id, rating in user_holdout.items()
                if rating >= threshold
            }

            # --- mood suite: global nudge vs max-over-modes nudge ---
            # Ranked within the mood's browsable genre pool: a 0.15-weight
            # nudge cannot lift a film 1000+ positions in a 20k scan, so the
            # between-pool protocol has no power; within-pool ordering is
            # what the nudge can actually move.
            for mood, phrase in MOOD_QUERIES.items():
                mood_genres = set(MOOD_PROFILES[mood]["genres"])
                pool = [
                    movie
                    for movie in catalog
                    if movie.genres
                    and mood_genres
                    & {g.strip() for g in movie.genres.split(",")}
                ]
                relevant = {
                    tmdb_id
                    for tmdb_id, rating in loved.items()
                    if tmdb_id in {m.tmdb_id for m in pool}
                }
                if not relevant or not pool:
                    continue
                for arm, arm_modes in (("global", None), ("modes", modes)):
                    _, _, results = search_svc.search(
                        db, phrase, pool, taste_vec, 20,
                        taste_modes=arm_modes,
                    )
                    recommended = [r["movie"].tmdb_id for r in results]
                    mood_rows[arm].append({
                        "user_id": user_id,
                        "mood": mood,
                        "ndcg20": _ndcg_at(recommended, loved, 20),
                        "recall10": _recall_at(recommended, relevant, 10),
                        "recall20": _recall_at(recommended, relevant, 20),
                        "mrr": _mrr(recommended, relevant),
                    })

            # --- anchor suite: taste-independent retrieval baseline ---
            anchors = sorted(
                (tmdb_id for tmdb_id, rating in loved.items() if tmdb_id in by_id),
            )[: args.anchors_per_persona]
            for anchor_id in anchors:
                relevant = set(loved) - {anchor_id}
                if not relevant:
                    continue
                anchor_row = by_id[anchor_id]
                _, _, results = search_svc.search(
                    db, anchor_row.title, catalog, None, 20, anchor=anchor_row
                )
                recommended = [r["movie"].tmdb_id for r in results]
                anchor_rows.append({
                    "user_id": user_id,
                    "anchor": anchor_id,
                    "ndcg20": _ndcg_at(recommended, loved, 20),
                    "recall10": _recall_at(recommended, relevant, 10),
                    "recall20": _recall_at(recommended, relevant, 20),
                    "mrr": _mrr(recommended, relevant),
                })

            if index % 10 == 0 or index == len(personas):
                print(f"  evaluated {index}/{len(personas)} personas")

        global_macro = _macro(mood_rows["global"])
        modes_macro = _macro(mood_rows["modes"])
        by_user_global = defaultdict(list)
        by_user_modes = defaultdict(list)
        for row in mood_rows["global"]:
            by_user_global[row["user_id"]].append(row)
        for row in mood_rows["modes"]:
            by_user_modes[row["user_id"]].append(row)
        deltas = {}
        for metric in ("ndcg20", "recall10", "recall20", "mrr"):
            pairs = []
            for user_id in sorted(set(by_user_global) & set(by_user_modes)):
                g = _macro(by_user_global[user_id])[metric]
                m = _macro(by_user_modes[user_id])[metric]
                if g is not None and m is not None:
                    pairs.append(m - g)
            mean = sum(pairs) / len(pairs) if pairs else None
            t = None
            if len(pairs) > 1 and mean is not None:
                s = (sum((x - mean) ** 2 for x in pairs) / (len(pairs) - 1)) ** 0.5
                t = mean / (s / len(pairs) ** 0.5) if s else None
            deltas[metric] = {
                "mean_delta": mean,
                "paired_t": t,
                "n_personas": len(pairs),
            }

        anchor_macro = _macro(anchor_rows)
        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_db": str(source),
            "catalog_films": len(catalog),
            "persona_count": len(personas),
            "personas_with_modes": personas_with_modes,
            "protocol": {
                "split": "same deterministic 20% user-level holdout as eval_recommender",
                "mood_relevance": (
                    "held-out films rated >= persona threshold whose genres "
                    "intersect the mood profile's genre set"
                ),
                "anchor_relevance": (
                    "the persona's other held-out films rated >= threshold"
                ),
            },
            "mood_suite": {
                "global_nudge": global_macro,
                "modes_nudge": modes_macro,
                "paired_delta_modes_minus_global": deltas,
            },
            "anchor_suite": anchor_macro,
            "runtime_seconds": round(time.monotonic() - started, 1),
        }

        def _jsonable(value):
            if isinstance(value, dict):
                return {k: _jsonable(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_jsonable(v) for v in value]
            if isinstance(value, (np.floating, np.integer)):
                return value.item()
            if isinstance(value, float):
                return None if math.isnan(value) else value
            return value

        report = _jsonable(report)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))

        md = [
            "# Search evaluation",
            "",
            f"- catalog: {len(catalog)} films, personas: {len(personas)} "
            f"({personas_with_modes} with taste modes)",
            f"- same 20% user-level holdout as the feed eval",
            "",
            "## Mood suite (gates search-side taste modes)",
            "",
            "| nudge | NDCG@20 | Recall@10 | Recall@20 | MRR |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, row in (("global vector", global_macro), ("max-over-modes", modes_macro)):
            md.append(
                f"| {name} | {row['ndcg20'] or 0:.4f} | {row['recall10'] or 0:.4f} | "
                f"{row['recall20'] or 0:.4f} | {row['mrr'] or 0:.4f} |"
            )
        md += ["", "Paired per-persona deltas (modes − global):", ""]
        for metric, d in deltas.items():
            if d["mean_delta"] is None:
                continue
            md.append(
                f"- {metric}: {d['mean_delta']:+.4f} "
                f"(t={d['paired_t'] if d['paired_t'] is not None else 0:+.2f}, "
                f"n={d['n_personas']})"
            )
        md += [
            "",
            "## Anchor suite (baseline, taste-independent)",
            "",
            f"- NDCG@20 {anchor_macro['ndcg20'] or 0:.4f} · "
            f"Recall@10 {anchor_macro['recall10'] or 0:.4f} · "
            f"Recall@20 {anchor_macro['recall20'] or 0:.4f} · "
            f"MRR {anchor_macro['mrr'] or 0:.4f} "
            f"({len(anchor_rows)} anchor queries)",
            "",
        ]
        args.out.with_suffix(".md").write_text("\n".join(md))

        print(
            f"mood: global NDCG@20={global_macro['ndcg20'] or 0:.4f} "
            f"modes NDCG@20={modes_macro['ndcg20'] or 0:.4f}"
        )
        print(
            f"anchor: NDCG@20={anchor_macro['ndcg20'] or 0:.4f} "
            f"Recall@10={anchor_macro['recall10'] or 0:.4f}"
        )
        print(f"report: {args.out}")
        return 0
    finally:
        db.close()
        for path in (
            args.eval_db,
            Path(str(args.eval_db) + "-wal"),
            Path(str(args.eval_db) + "-shm"),
        ):
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
