"""Offline holdout check for the behavior graph.

The important question is not "does the graph look plausible on The Dark
Knight?" but "does walking the graph from a viewer's training films retrieve
films they actually rated in the holdout better than popularity or the
metadata graph?"  This script answers that with a deterministic rating holdout.

It does not write to the live DB and does not touch the production artifact.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from scripts.build_behavior_graph import (  # noqa: E402
    build_adjacency,
    build_similarities,
    combine_direct,
    diffuse,
    load_catalog,
    load_interactions,
)


def split_ratings(ratings, holdout=0.2, seed=42):
    rng = random.Random(seed)
    train = {}
    test = {}
    for user, items in ratings.items():
        films = sorted(items)
        rng.shuffle(films)
        cut = max(1, int(len(films) * holdout)) if len(films) >= 2 else 0
        held = set(films[:cut])
        train[user] = {film: rating for film, rating in items.items() if film not in held}
        test[user] = {film: items[film] for film in held}
    return train, test


def graph_degrees(graph):
    degree = Counter()
    for neighbors in graph.values():
        for neighbor in neighbors:
            degree[neighbor] += 1
    return degree


def aggregate_scores(seed_ids, graph, max_hops=1, degree=None, penalty=0.0):
    scores = defaultdict(float)
    for seed in seed_ids:
        if max_hops <= 1:
            neighbors = graph.get(seed, {})
            for neighbor, score in neighbors.items():
                scores[neighbor] += score
        else:
            for neighbor, payload in diffuse(graph, seed, max_hops=max_hops).items():
                scores[neighbor] += payload["score"]
    if seed_ids:
        scale = 1.0 / len(seed_ids)
        scores = {film: score * scale for film, score in scores.items()}
    if degree and penalty:
        scores = {
            film: score / (1.0 + penalty * math.log1p(degree.get(film, 0)))
            for film, score in scores.items()
        }
    return scores


def rank_metrics(scores, heldout, train_ids, k=20):
    ranked = [
        film
        for film, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if film not in train_ids
    ]
    top = ranked[:k]
    hits = sum(1 for film in heldout if film in top)
    recall = hits / max(1, len(heldout))
    # A small graded metric: each held-out hit contributes 1/log2(rank+1).
    dcg = 0.0
    for rank, film in enumerate(top, 1):
        if film in heldout:
            dcg += 1.0 / math.log2(rank + 1)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(heldout), k) + 1))
    ndcg = dcg / ideal if ideal else 0.0
    return hits, recall, ndcg, len(ranked)


def evaluate(
    name,
    graph,
    users,
    train,
    test,
    target_ids,
    max_hops=1,
    degree_penalty=0.0,
):
    degree = graph_degrees(graph)
    hits_total = 0
    test_total = 0
    users_with_hit = 0
    recalls = []
    ndcgs = []
    candidate_counts = []
    for user in users:
        heldout = set(test.get(user, {}))
        if not heldout:
            continue
        train_items = train.get(user, {})
        if not train_items:
            continue
        seeds = [
            film for film, rating in train_items.items() if rating >= 3.5
        ]
        if not seeds:
            seeds = list(train_items)
        # Cap seeds to the strongest-rated subset, matching how the live feed
        # treats a viewer's positive history.
        seeds = sorted(
            seeds,
            key=lambda film: (train_items[film], film),
            reverse=True,
        )[:400]
        scores = aggregate_scores(
            seeds,
            graph,
            max_hops=max_hops,
            degree=degree,
            penalty=degree_penalty,
        )
        scores = {film: score for film, score in scores.items() if film in target_ids}
        hits, recall, ndcg, candidates = rank_metrics(
            scores, heldout, set(train_items), k=20
        )
        hits_total += hits
        test_total += len(heldout)
        users_with_hit += 1 if hits else 0
        recalls.append(recall)
        ndcgs.append(ndcg)
        candidate_counts.append(candidates)
    return {
        "name": name,
        "users": len(recalls),
        "max_hops": max_hops,
        "degree_penalty": degree_penalty,
        "hit@20": hits_total,
        "test_items": test_total,
        "micro_recall@20": round(hits_total / max(1, test_total), 4),
        "macro_recall@20": round(sum(recalls) / max(1, len(recalls)), 4),
        "ndcg@20": round(sum(ndcgs) / max(1, len(ndcgs)), 4),
        "users_with_a_hit": users_with_hit,
        "avg_candidates": round(sum(candidate_counts) / max(1, len(candidate_counts)), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=BACKEND / "cinematch.db")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="top Letterboxd ranks for a pilot; 0 evaluates the full catalog",
    )
    parser.add_argument("--holdout", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = load_catalog(con, args.limit)
    target_ids = {int(row["tmdb_id"]) for row in rows}
    collection_ids = {
        int(row["tmdb_id"]): int(row["collection_id"])
        for row in rows
        if row.get("collection_id")
    }
    ratings, likes, watches = load_interactions(con, target_ids)
    train, test = split_ratings(ratings, args.holdout, args.seed)
    users = sorted(set(train) | set(test))
    if len(users) < 2:
        print("not enough users", file=sys.stderr)
        return 2

    # Remove held-out ratings from the implicit signals too.  Likes and watches
    # are not the prediction target here, but leaving the target film in them
    # would leak the answer into the graph.
    test_by_user = {user: set(items) for user, items in test.items()}
    train_likes = {
        user: {film for film in films if film not in test_by_user.get(user, set())}
        for user, films in likes.items()
    }
    train_watches = {
        user: {film for film in films if film not in test_by_user.get(user, set())}
        for user, films in watches.items()
    }

    (
        rating_sims,
        like_sims,
        like_weighted_sims,
        watch_sims,
        rating_totals,
        like_totals,
        watch_totals,
        _users,
    ) = build_similarities(target_ids, train, train_likes, train_watches)
    direct_rows, support = combine_direct(
        target_ids,
        collection_ids,
        rating_sims,
        like_sims,
        like_weighted_sims,
        watch_sims,
        rating_totals,
        like_totals,
        watch_totals,
    )
    behavior_graph = build_adjacency(direct_rows)

    metadata_graph = defaultdict(dict)
    for anchor, neighbor, score in con.execute(
        "select tmdb_id, neighbor_tmdb_id, score from film_neighbors "
        "where source = 'hybrid_v1'"
    ):
        anchor = int(anchor)
        neighbor = int(neighbor)
        if anchor in target_ids and neighbor in target_ids:
            metadata_graph[anchor][neighbor] = max(
                metadata_graph[anchor].get(neighbor, 0.0), float(score)
            )

    # Popularity is deliberately local (training interactions), not TMDB
    # popularity: the comparison should isolate the graph's contribution.
    train_popularity = Counter()
    for user, items in train.items():
        train_popularity.update(items)
    for user, items in train_likes.items():
        train_popularity.update(items)
    for user, items in train_watches.items():
        train_popularity.update(items)
    popularity_scores = {
        film: count for film, count in train_popularity.items() if film in target_ids
    }

    results = [
        evaluate("behavior_direct", behavior_graph, users, train, test, target_ids, 1),
        evaluate(
            "behavior_direct_degree",
            behavior_graph,
            users,
            train,
            test,
            target_ids,
            1,
            degree_penalty=0.5,
        ),
        evaluate("behavior_2hop", behavior_graph, users, train, test, target_ids, 2),
        evaluate(
            "behavior_3hop",
            behavior_graph,
            users,
            train,
            test,
            target_ids,
            3,
            degree_penalty=0.5,
        ),
        evaluate("metadata_hybrid", metadata_graph, users, train, test, target_ids, 1),
    ]
    # Popularity is evaluated through its own full-catalog score map below, so
    # the comparison uses exactly the same ranking function.
    pop_hits = 0
    pop_total = 0
    pop_users = 0
    pop_recalls = []
    pop_ndcgs = []
    for user in users:
        heldout = set(test.get(user, {}))
        if not heldout:
            continue
        train_ids = set(train.get(user, {}))
        if not train_ids:
            continue
        scores = {
            film: score for film, score in popularity_scores.items() if film not in train_ids
        }
        hits, recall, ndcg, _ = rank_metrics(scores, heldout, train_ids, k=20)
        pop_hits += hits
        pop_total += len(heldout)
        pop_users += 1 if hits else 0
        pop_recalls.append(recall)
        pop_ndcgs.append(ndcg)
    results[-1] = {
        "name": "local_popularity",
        "users": len(pop_recalls),
        "hit@20": pop_hits,
        "test_items": pop_total,
        "micro_recall@20": round(pop_hits / max(1, pop_total), 4),
        "macro_recall@20": round(sum(pop_recalls) / max(1, len(pop_recalls)), 4),
        "ndcg@20": round(sum(pop_ndcgs) / max(1, len(pop_ndcgs)), 4),
        "users_with_a_hit": pop_users,
    }

    report = {
        "db": str(args.db),
        "limit": args.limit,
        "holdout": args.holdout,
        "seed": args.seed,
        "users": len(users),
        "train_ratings": sum(len(items) for items in train.values()),
        "test_ratings": sum(len(items) for items in test.values()),
        "behavior_direct_anchors": len(direct_rows),
        "behavior_direct_edges": support["edges"],
        "metadata_edges": sum(len(row) for row in metadata_graph.values()),
        "results": results,
    }
    print(json.dumps(report, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
