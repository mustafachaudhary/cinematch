"""Offline recommender evaluation for CineMatch.

Protocol (v0):
* one deterministic, user-level holdout per persona (20%, at least 20 and at
  most 200 ratings per user);
* the held-out rows are deleted from the *evaluation copy's* ratings matrix,
  so item similarities, the taste vector, and CF predictions are rebuilt from
  training ratings only;
* the persona's whole watch history is excluded from warm candidates except
  for the held-out test films, which must remain rankable;
* the cold-start arm scores the quality-ranked feed against the persona's full
  history (a simulated brand-new user; it may recommend already-seen films).

Metrics are reported for graded stars and a binary relevance threshold
``max(3.5, training_mean)``: NDCG@20, Recall@20/50, MRR, CF MAE/coverage,
catalog coverage, popularity bias, intra-list similarity, and CF-support share.

Usage:
    cd backend && ../.venv/bin/python scripts/eval_recommender.py \
        --db /tmp/opencode/cinematch_seed_import/cinematch_seed_import.db \
        --out reports/eval_baseline.json

The source DB is never modified. The evaluation copy is removed unless
``--keep-eval-db`` is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
sys.path.insert(0, str(BACKEND))


# ---------------------------------------------------------------------------
# Evaluation copy + deterministic split
# ---------------------------------------------------------------------------


def _split_key(user_id: int, tmdb_id: int) -> str:
    return hashlib.sha1(f"{user_id}:{tmdb_id}".encode("utf-8")).hexdigest()


def _table_names(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def prepare_eval_db(
    source: Path,
    destination: Path,
    *,
    test_frac: float,
    min_test: int,
    max_test: int,
    min_ratings: int,
    include_mustafa: bool,
) -> list[dict]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for path in (
        destination,
        Path(str(destination) + "-wal"),
        Path(str(destination) + "-shm"),
    ):
        if path.exists():
            path.unlink()

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    src.backup(dst)
    src.close()

    dst.execute("pragma journal_mode=wal")
    dst.execute("pragma synchronous=normal")
    dst.execute("pragma busy_timeout=30000")
    tables = _table_names(dst)
    if "seed_profile" not in tables:
        raise RuntimeError("source DB has no seed_profile table; import the new cohort first")

    dst.execute("drop table if exists eval_personas")
    dst.execute("drop table if exists eval_holdout")
    dst.execute("drop table if exists eval_full_history")
    dst.execute(
        """
        create table eval_personas (
            user_id integer primary key,
            username text not null,
            cohort text,
            n_train integer not null,
            n_test integer not null,
            mean_train real not null,
            threshold real not null
        )
        """
    )
    dst.execute(
        """
        create table eval_holdout (
            user_id integer not null,
            tmdb_id integer not null,
            rating real not null,
            primary key (user_id, tmdb_id)
        )
        """
    )
    dst.execute(
        """
        create table eval_full_history (
            user_id integer not null,
            tmdb_id integer not null,
            rating real not null,
            primary key (user_id, tmdb_id)
        )
        """
    )

    personas = []
    seed_rows = dst.execute(
        """
        select u.user_id, u.username, s.cohort
        from users u
        join seed_profile s on s.user_id = u.user_id
        where u.is_seed = 1
        order by u.username
        """
    ).fetchall()
    if include_mustafa:
        row = dst.execute(
            "select user_id, username from users where username = 'mustafa'"
        ).fetchone()
        if row:
            seed_rows = list(seed_rows) + [(row[0], row[1], "real")]

    for user_id, username, cohort in seed_rows:
        rows = dst.execute(
            "select tmdb_id, rating from logged_movies where user_id = ? order by tmdb_id",
            (user_id,),
        ).fetchall()
        if len(rows) < min_ratings:
            continue
        ordered = sorted(rows, key=lambda item: _split_key(user_id, int(item[0])))
        n_test = max(min_test, int(round(len(ordered) * test_frac)))
        n_test = min(n_test, max_test, max(1, len(ordered) - 20))
        test = ordered[:n_test]
        train = ordered[n_test:]

        mean_train = sum(float(r) for _, r in train) / len(train)
        threshold = max(3.5, mean_train)

        dst.executemany(
            "insert into eval_full_history (user_id, tmdb_id, rating) values (?, ?, ?)",
            [(user_id, int(t), float(r)) for t, r in rows],
        )
        dst.executemany(
            "insert into eval_holdout (user_id, tmdb_id, rating) values (?, ?, ?)",
            [(user_id, int(t), float(r)) for t, r in test],
        )
        dst.execute(
            "insert into eval_personas "
            "(user_id, username, cohort, n_train, n_test, mean_train, threshold) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                username,
                cohort,
                len(train),
                len(test),
                mean_train,
                threshold,
            ),
        )

        test_ids = [int(t) for t, _ in test]
        placeholders = ",".join("?" for _ in test_ids)
        dst.execute(
            f"delete from logged_movies where user_id = ? and tmdb_id in ({placeholders})",
            (user_id, *test_ids),
        )
        if "ratings" in tables:
            dst.execute(
                f"delete from ratings where user_id = ? and film_id in ({placeholders})",
                (user_id, *test_ids),
            )
        if "user_likes" in tables:
            dst.execute(
                f"delete from user_likes where user_id = ? and tmdb_id in ({placeholders})",
                (user_id, *test_ids),
            )
        if "watched_movies" in tables:
            dst.execute(
                f"delete from watched_movies where user_id = ? and tmdb_id in ({placeholders})",
                (user_id, *test_ids),
            )

        personas.append(
            {
                "user_id": user_id,
                "username": username,
                "cohort": cohort or "unknown",
                "n_train": len(train),
                "n_test": len(test),
                "mean_train": round(mean_train, 4),
                "threshold": round(threshold, 4),
            }
        )

    dst.commit()
    dst.execute("vacuum")
    dst.commit()
    dst.close()
    return personas


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _gain(rating: float | None) -> float:
    if rating is None or rating <= 0:
        return 0.0
    return float(2.0 ** (float(rating) - 0.5) - 1.0)


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def _ndcg_at(recommended: list[int], ratings: dict[int, float], k: int, *, binary: bool, threshold: float) -> float | None:
    if not ratings:
        return None
    if binary:
        actual = [1.0 if ratings.get(tmdb_id, 0.0) >= threshold else 0.0 for tmdb_id in recommended[:k]]
        ideal = [1.0] * min(k, sum(1 for value in ratings.values() if value >= threshold))
    else:
        actual = [_gain(ratings.get(tmdb_id)) for tmdb_id in recommended[:k]]
        ideal = sorted((_gain(value) for value in ratings.values()), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    if ideal_dcg <= 0:
        return None
    return _dcg(actual) / ideal_dcg


def _recall_at(recommended: list[int], relevant: set[int], k: int) -> float | None:
    if not relevant:
        return None
    return len(set(recommended[:k]) & relevant) / len(relevant)


def _mrr(recommended: list[int], relevant: set[int]) -> float:
    for index, tmdb_id in enumerate(recommended, 1):
        if tmdb_id in relevant:
            return 1.0 / index
    return 0.0


def _mean(values: list[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def _macro(rows: list[dict]) -> dict:
    return {
        "ndcg20_graded": _mean([row.get("ndcg20_graded") for row in rows]),
        "ndcg20_binary": _mean([row.get("ndcg20_binary") for row in rows]),
        "recall20": _mean([row.get("recall20") for row in rows]),
        "recall50": _mean([row.get("recall50") for row in rows]),
        "mrr": _mean([row.get("mrr") for row in rows]),
        "cf_mae": _weighted_mean(rows, "cf_mae", "cf_mae_n"),
        "cf_coverage": _mean([row.get("cf_coverage") for row in rows]),
        "cf_support_share": _mean([row.get("cf_support_share") for row in rows]),
        "mean_popularity": _mean([row.get("mean_popularity") for row in rows]),
        "intra_list_similarity": _mean([row.get("intra_list_similarity") for row in rows]),
    }


def _weighted_mean(rows: list[dict], value_key: str, weight_key: str) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        value = row.get(value_key)
        weight = row.get(weight_key) or 0
        if value is None or not weight:
            continue
        numerator += float(value) * int(weight)
        denominator += int(weight)
    return numerator / denominator if denominator else None


def _intra_list_similarity(movies: list) -> float | None:
    from services.embeddings import decode_embedding

    vectors = [decode_embedding(movie.embedding) for movie in movies[:20]]
    if len(vectors) < 2 or any(vector is None for vector in vectors):
        return None
    matrix = np.vstack(vectors).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    similarity = matrix @ matrix.T
    upper = similarity[np.triu_indices(len(vectors), k=1)]
    return float(np.mean(upper)) if upper.size else None


def _evaluate_warm(db, persona: dict, catalog: list, holdout: dict[int, float]):
    from models.database import LoggedMovie, Review, UserLike, WatchedMovie
    from services import collab, embeddings, ranking, taste

    user_id = persona["user_id"]
    train_ids = {
        int(row[0])
        for row in db.query(LoggedMovie.tmdb_id)
        .filter(LoggedMovie.user_id == user_id)
        .all()
    }
    watched_ids = {
        int(row[0])
        for row in db.query(WatchedMovie.tmdb_id)
        .filter(WatchedMovie.user_id == user_id)
        .all()
        if row[0] is not None
    }
    liked_ids = {
        int(row[0])
        for row in db.query(UserLike.tmdb_id)
        .filter(UserLike.user_id == user_id)
        .all()
    }
    test_ids = set(holdout)
    excluded = (train_ids | watched_ids | liked_ids) - test_ids
    candidates = [movie for movie in catalog if movie.tmdb_id not in excluded]

    compounds = {
        int(row[0]): float(row[1] or 0.0)
        for row in db.query(Review.tmdb_id, Review.sentiment_compound)
        .filter(Review.user_id == user_id)
        .all()
    }
    rated_view = taste.load_rated_movies(db, user_id)
    results = ranking.rank_feed(
        db,
        user_id,
        candidates,
        limit=50,
        rated_view=rated_view,
        compounds=compounds,
    )
    recommended = [int(result["movie"].tmdb_id) for result in results]
    top_movies = [result["movie"] for result in results]

    threshold = float(persona["threshold"])
    relevant = {tmdb_id for tmdb_id, rating in holdout.items() if rating >= threshold}
    predictions, _ = collab.get_predictions(db, user_id)
    errors = [
        abs(float(predictions[tmdb_id]["pred"]) - float(rating))
        for tmdb_id, rating in holdout.items()
        if tmdb_id in predictions
    ]

    top20 = top_movies[:20]
    cf_support = sum(
        1 for result in results[:20] if "cf" in (result.get("signals") or {})
    ) / max(1, len(results[:20]))
    popularity = [float(movie.popularity or 0.0) for movie in top20]

    return {
        "user_id": user_id,
        "username": persona["username"],
        "cohort": persona["cohort"],
        "n_train": persona["n_train"],
        "n_test": persona["n_test"],
        "threshold": threshold,
        "ndcg20_graded": _ndcg_at(recommended, holdout, 20, binary=False, threshold=threshold),
        "ndcg20_binary": _ndcg_at(recommended, holdout, 20, binary=True, threshold=threshold),
        "recall20": _recall_at(recommended, relevant, 20),
        "recall50": _recall_at(recommended, relevant, 50),
        "mrr": _mrr(recommended, relevant),
        "cf_mae": (sum(errors) / len(errors)) if errors else None,
        "cf_mae_n": len(errors),
        "cf_coverage": len(errors) / len(holdout) if holdout else None,
        "cf_support_share": cf_support,
        "mean_popularity": (sum(popularity) / len(popularity)) if popularity else None,
        "intra_list_similarity": _intra_list_similarity(top_movies),
        "recommended": recommended[:20],
    }


def _evaluate_cold(persona: dict, catalog: list, full_history: dict[int, float]):
    from services import ranking

    results = ranking.cold_start_feed(catalog, 50)
    recommended = [int(result["movie"].tmdb_id) for result in results]
    threshold = float(persona["threshold"])
    relevant = {
        tmdb_id for tmdb_id, rating in full_history.items() if rating >= threshold
    }
    return {
        "user_id": persona["user_id"],
        "username": persona["username"],
        "cohort": persona["cohort"],
        "ndcg20_graded": _ndcg_at(recommended, full_history, 20, binary=False, threshold=threshold),
        "ndcg20_binary": _ndcg_at(recommended, full_history, 20, binary=True, threshold=threshold),
        "recall20": _recall_at(recommended, relevant, 20),
        "recall50": _recall_at(recommended, relevant, 50),
        "mrr": _mrr(recommended, relevant),
    }


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float):
        return None if math.isnan(value) else value
    return value


def _markdown(report: dict) -> str:
    warm = report["aggregate"]["warm"]
    cold = report["aggregate"]["cold"]
    lines = [
        f"# {report.get('label', 'CineMatch evaluation')}",
        "",
        f"- source: `{report['source_db']}`",
        f"- catalog: {report['catalog_films']} films",
        f"- personas: {report['persona_count']}",
        f"- ranker: {report.get('ranker', {}).get('version', 'unknown')} "
        f"(disabled: {', '.join(report.get('ranker', {}).get('disabled', [])) or 'none'})",
        f"- split: {report['protocol']['test_frac']:.0%} per user, "
        f"{report['protocol']['min_test']}–{report['protocol']['max_test']} rows",
        "",
        "| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in (("warm", warm), ("cold", cold)):
        lines.append(
            f"| {name} | {row.get('ndcg20_graded') or 0:.4f} | "
            f"{row.get('ndcg20_binary') or 0:.4f} | "
            f"{row.get('recall20') or 0:.4f} | "
            f"{row.get('recall50') or 0:.4f} | "
            f"{row.get('mrr') or 0:.4f} | "
            f"{row.get('cf_mae') or 0:.4f} | "
            f"{row.get('cf_coverage') or 0:.2%} |"
        )
    lines += [
        "",
        f"- catalog coverage (warm): {report['catalog_coverage_warm']:.2%}",
        f"- mean popularity (warm): {warm.get('mean_popularity') or 0:.2f}",
        f"- intra-list similarity (warm): {warm.get('intra_list_similarity') or 0:.4f}",
        f"- CF support share (warm): {warm.get('cf_support_share') or 0:.2%}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--eval-db", type=Path, default=Path("/tmp/opencode/cinematch_eval/eval.db"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--test-frac", type=float, default=0.2)
    parser.add_argument("--min-test", type=int, default=20)
    parser.add_argument("--max-test", type=int, default=200)
    parser.add_argument("--min-ratings", type=int, default=100)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-mustafa", action="store_true", default=True)
    parser.add_argument("--no-mustafa", dest="include_mustafa", action="store_false")
    parser.add_argument("--keep-eval-db", action="store_true")
    parser.add_argument("--allow-missing-embeddings", action="store_true")
    parser.add_argument("--label", default="CineMatch evaluation")
    args = parser.parse_args()

    source = args.db.resolve()
    if not source.exists():
        print(f"source DB does not exist: {source}", file=sys.stderr)
        return 2

    started = time.monotonic()
    personas = prepare_eval_db(
        source,
        args.eval_db,
        test_frac=args.test_frac,
        min_test=args.min_test,
        max_test=args.max_test,
        min_ratings=args.min_ratings,
        include_mustafa=args.include_mustafa,
    )
    if not personas:
        print("no evaluation personas found", file=sys.stderr)
        return 2

    os.environ["DATABASE_URL"] = f"sqlite:///{args.eval_db}"
    from sqlalchemy import func

    from models.database import MovieCache, SessionLocal
    from services import embeddings, ranking, taste

    db = SessionLocal()
    try:
        catalog = db.query(MovieCache).all()
        missing = db.query(func.count(MovieCache.tmdb_id)).filter(
            MovieCache.embedding.is_(None)
        ).scalar()
        if missing and not args.allow_missing_embeddings:
            print(
                f"{missing} catalog films have no embedding; run embed_movies on the "
                "import copy first (or pass --allow-missing-embeddings, which may be slow).",
                file=sys.stderr,
            )
            return 2

        holdout: dict[int, dict[int, float]] = defaultdict(dict)
        full: dict[int, dict[int, float]] = defaultdict(dict)
        con = sqlite3.connect(f"file:{args.eval_db}?mode=ro", uri=True)
        try:
            for user_id, tmdb_id, rating in con.execute(
                "select user_id, tmdb_id, rating from eval_holdout"
            ):
                holdout[int(user_id)][int(tmdb_id)] = float(rating)
            for user_id, tmdb_id, rating in con.execute(
                "select user_id, tmdb_id, rating from eval_full_history"
            ):
                full[int(user_id)][int(tmdb_id)] = float(rating)
        finally:
            con.close()

        warm_rows = []
        cold_rows = []
        cold_results = ranking.cold_start_feed(catalog, args.limit)
        cold_recommended = [int(result["movie"].tmdb_id) for result in cold_results]

        for index, persona in enumerate(personas, 1):
            user_holdout = holdout.get(persona["user_id"], {})
            if not user_holdout:
                continue
            row = _evaluate_warm(db, persona, catalog, user_holdout)
            warm_rows.append(row)

            user_full = full.get(persona["user_id"], {})
            threshold = float(persona["threshold"])
            relevant = {tmdb_id for tmdb_id, rating in user_full.items() if rating >= threshold}
            cold_rows.append(
                {
                    "user_id": persona["user_id"],
                    "username": persona["username"],
                    "cohort": persona["cohort"],
                    "ndcg20_graded": _ndcg_at(
                        cold_recommended, user_full, 20, binary=False, threshold=threshold
                    ),
                    "ndcg20_binary": _ndcg_at(
                        cold_recommended, user_full, 20, binary=True, threshold=threshold
                    ),
                    "recall20": _recall_at(cold_recommended, relevant, 20),
                    "recall50": _recall_at(cold_recommended, relevant, 50),
                    "mrr": _mrr(cold_recommended, relevant),
                }
            )
            if index % 10 == 0 or index == len(personas):
                print(f"  evaluated {index}/{len(personas)} personas")

        by_cohort = {}
        for cohort in sorted({row["cohort"] for row in warm_rows}):
            by_cohort[cohort] = _macro([row for row in warm_rows if row["cohort"] == cohort])

        recommended_ids = {
            tmdb_id for row in warm_rows for tmdb_id in row.get("recommended", [])
        }
        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "label": args.label,
            "source_db": str(source),
            "eval_db": str(args.eval_db),
            "catalog_films": len(catalog),
            "persona_count": len(warm_rows),
            "ranker": ranking.describe(),
            "protocol": {
                "test_frac": args.test_frac,
                "min_test": args.min_test,
                "max_test": args.max_test,
                "min_ratings": args.min_ratings,
                "split": "deterministic sha1(user_id:tmdb_id), one holdout per user",
                "leakage_note": (
                    "Held-out user edges are removed before item similarities are built. "
                    "The held-out film can still be present in other users' histories; "
                    "this is a user-level, not item-level, holdout."
                ),
            },
            "aggregate": {
                "warm": _macro(warm_rows),
                "cold": _macro(cold_rows),
                "by_cohort": by_cohort,
            },
            "catalog_coverage_warm": (
                len(recommended_ids) / len(catalog) if catalog else 0.0
            ),
            "personas": warm_rows,
            "cold_personas": cold_rows,
            "runtime_seconds": round(time.monotonic() - started, 1),
        }
        report = _jsonable(report)

        out_path = args.out or (
            BACKEND / "reports" / f"eval_baseline_{args.tag}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))
        md_path = out_path.with_suffix(".md")
        md_path.write_text(_markdown(report))

        warm = report["aggregate"]["warm"]
        print(
            f"warm: NDCG@20={warm['ndcg20_graded']:.4f} "
            f"Recall@20={warm['recall20'] or 0:.4f} "
            f"MRR={warm['mrr'] or 0:.4f} "
            f"CF-MAE={warm['cf_mae'] or 0:.4f} "
            f"coverage={report['catalog_coverage_warm']:.2%}"
        )
        print(f"report: {out_path}")
        print(f"markdown: {md_path}")
        return 0
    finally:
        db.close()
        if not args.keep_eval_db:
            for path in (
                args.eval_db,
                Path(str(args.eval_db) + "-wal"),
                Path(str(args.eval_db) + "-shm"),
            ):
                if path.exists():
                    path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
