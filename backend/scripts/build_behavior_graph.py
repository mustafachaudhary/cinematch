"""Build the behavior-first local relationship graph.

This is deliberately *not* a metadata graph with a CF tie-breaker.  The
catalog already has the evidence we need: ratings, likes and watches from the
local cohort.  We use that evidence to create the relationships first, then
walk two and three hops through the behavior graph so that a strong chain of
shared viewers can surface a film that is not a direct co-rating neighbour of
the anchor.

Artifacts
---------
``film_neighbors`` rows with ``source='behavior_v1'``
    Durable local ``more like this`` edges.  Each edge records the direct
    co-rating/co-like/co-watch support and, for indirect edges, the path
    strength and bridge film.

``behavior_clusters`` / ``film_behavior_subniches``
    Soft subniches fitted with NMF on the local user x film preference matrix.
    They are local taste communities, not Letterboxd themes or TMDB keywords.

Local behavior is the edge creator.  The only structural prior is the
existing collection id, and it is applied only after a pair already has
behavioral support: it keeps a sequel/prequel from being pushed out by a broad
co-like cluster without allowing a metadata-only relation into this graph.
Cold films keep the existing ``hybrid_v1`` graph as a fallback; the API uses
behavior edges first and fills the remaining shelf slots from the metadata
graph.

Usage:
    cd backend && ../.venv/bin/python scripts/build_behavior_graph.py \
        --db /tmp/opencode/cinematch_behavior_pilot.db --limit 3000 --dry-run
    ... --db cinematch.db --allow-live
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
sys.path.insert(0, str(BACKEND))

from services.collab import _baseline_residuals, _baseline_stats  # noqa: E402

RATING_SHRINKAGE = 8.0
LIKE_SHRINKAGE = 5.0
WATCH_SHRINKAGE = 10.0
DIRECT_TOP_K = 80
SIGNAL_TOP_K = 100
DEFAULT_NEIGHBORS = 48
DEFAULT_DIRECT_NEIGHBORS = 24
RATING_WEIGHT = 0.30
RATING_JACCARD_WEIGHT = 0.10
LIKE_JACCARD_WEIGHT = 0.28
LIKE_COSINE_WEIGHT = 0.22
WATCH_WEIGHT = 0.05
COLLECTION_PRIOR = 0.16
HOP2_DECAY = 0.45
HOP3_DECAY = 0.25


def json_default(default, value):
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_catalog(con, limit: int):
    if limit:
        rows = [
            dict(row)
            for row in con.execute(
                """
                select m.tmdb_id, m.title, m.release_year, m.collection_id
                  from movie_cache m
                  join letterboxd_popularity p on p.tmdb_id = m.tmdb_id
                 where p.rank <= ?
                 order by p.rank
                """,
                (limit,),
            )
        ]
    else:
        rows = [
            dict(row)
            for row in con.execute(
                "select tmdb_id, title, release_year, collection_id from movie_cache"
            )
        ]
    return rows


def load_interactions(con, target_ids):
    ratings = defaultdict(dict)
    for user_id, film_id, rating in con.execute(
        "select user_id, film_id, rating from ratings"
    ):
        film_id = int(film_id)
        if film_id in target_ids:
            ratings[int(user_id)][film_id] = float(rating)

    likes = defaultdict(set)
    for user_id, film_id in con.execute("select user_id, tmdb_id from user_likes"):
        film_id = int(film_id)
        if film_id in target_ids:
            likes[int(user_id)].add(film_id)

    watches = defaultdict(set)
    for user_id, film_id in con.execute(
        "select user_id, tmdb_id from watched_movies where tmdb_id is not null"
    ):
        film_id = int(film_id)
        if film_id in target_ids:
            watches[int(user_id)].add(film_id)

    # Logged-but-unrated films are implicit watches too.  Rated logs already
    # live in ``ratings`` and would otherwise be counted three times.
    for user_id, film_id in con.execute(
        "select user_id, tmdb_id from logged_movies where rating is null"
    ):
        film_id = int(film_id)
        if film_id in target_ids:
            watches[int(user_id)].add(film_id)

    return ratings, likes, watches


def _fit_film_users(matrix, user_ids):
    """Turn {user: {film: value}} into a film x user dense matrix."""
    films = sorted({film for items in matrix.values() for film in items})
    if not films:
        return [], [], np.zeros((0, len(user_ids)), dtype=np.float32)
    film_index = {film: index for index, film in enumerate(films)}
    user_index = {user: index for index, user in enumerate(user_ids)}
    values = np.zeros((len(films), len(user_ids)), dtype=np.float32)
    for user, items in matrix.items():
        column = user_index.get(user)
        if column is None:
            continue
        for film, value in items.items():
            row = film_index.get(film)
            if row is not None:
                values[row, column] = value
    return films, film_index, values


def _cosine_topk(values, mask, films, k, shrinkage, chunk_size=512):
    """Top-k positive cosine neighbours with co-occurrence shrinkage."""
    if not films:
        return {}
    norms = np.linalg.norm(values, axis=1)
    inv = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    normalized = values * inv[:, None]
    source = np.asarray(films)
    result = {}
    k = min(k, max(1, len(films) - 1))
    for start in range(0, len(films), chunk_size):
        end = min(start + chunk_size, len(films))
        cosine = normalized[start:end] @ normalized.T
        co = mask[start:end] @ mask.T
        for local, anchor_index in enumerate(range(start, end)):
            row = cosine[local]
            co_row = co[local]
            keep = (co_row >= 2) & (row > 0)
            keep[anchor_index] = False
            if not keep.any():
                continue
            weighted = np.where(
                keep,
                row * (co_row / (co_row + shrinkage)),
                0.0,
            )
            if int(keep.sum()) > k:
                chosen = np.argpartition(-weighted, k)[:k]
            else:
                chosen = np.flatnonzero(keep)
            chosen = chosen[weighted[chosen] > 0]
            if not chosen.size:
                continue
            chosen = chosen[np.argsort(-weighted[chosen])]
            anchor = int(source[anchor_index])
            result[anchor] = {
                int(source[index]): (
                    float(weighted[index]),
                    int(co_row[index]),
                )
                for index in chosen
            }
    return result


def build_similarities(target_ids, ratings, likes, watches):
    users = sorted(set(ratings) | set(likes) | set(watches))
    if len(users) < 2:
        return {}, {}, {}, {}, {}, {}, {}, users

    rating_films, _, rating_values = _fit_film_users(ratings, users)
    if rating_values.size:
        baselines = _baseline_stats(ratings)
        residuals = _baseline_residuals(ratings, baselines)
        residuals_films, _, residual_values = _fit_film_users(residuals, users)
        # Keep only above-baseline residuals.  Co-dislike is a valid signal for
        # clustering, but it should not create a "more like this" edge.
        residual_values = np.maximum(residual_values, 0.0)
        # Co-rating support means "both viewers rated it above their own
        # baseline".  Counting a viewer who disliked the film would make a
        # popular-film pair look better supported than it is.
        rating_mask = (residual_values > 0).astype(np.float32)
    else:
        residuals_films, residual_values, rating_mask = [], np.zeros((0, len(users))), np.zeros((0, len(users)))

    like_films, _, like_values = _fit_film_users(
        {user: {film: 1.0 for film in films} for user, films in likes.items()},
        users,
    )
    watch_films, _, watch_values = _fit_film_users(
        {user: {film: 1.0 for film in films} for user, films in watches.items()},
        users,
    )

    rating_sims = _cosine_topk(
        residual_values,
        rating_mask,
        residuals_films,
        SIGNAL_TOP_K,
        RATING_SHRINKAGE,
    )
    like_sims = _cosine_topk(
        like_values,
        like_values,
        like_films,
        SIGNAL_TOP_K,
        LIKE_SHRINKAGE,
    )
    # Users who like hundreds of films are less informative than users with a
    # tight, distinctive like list.  The weighted cosine makes the local graph
    # reflect taste rather than raw popularity.
    like_counts = np.asarray([len(likes.get(user, set())) for user in users], dtype=np.float32)
    like_weights = 1.0 / np.sqrt(np.maximum(like_counts, 1.0))
    like_weighted = like_values * like_weights[None, :]
    like_weighted_sims = _cosine_topk(
        like_weighted,
        like_values,
        like_films,
        SIGNAL_TOP_K,
        LIKE_SHRINKAGE,
    )
    watch_sims = _cosine_topk(
        watch_values,
        watch_values,
        watch_films,
        SIGNAL_TOP_K,
        WATCH_SHRINKAGE,
    )
    if not rating_values.size:
        rating_sims = {}

    rating_totals = {
        int(film): int(rating_mask[index].sum())
        for index, film in enumerate(residuals_films)
    }
    like_totals = {
        int(film): int(like_values[index].sum())
        for index, film in enumerate(like_films)
    }
    watch_totals = {
        int(film): int(watch_values[index].sum())
        for index, film in enumerate(watch_films)
    }
    return (
        rating_sims,
        like_sims,
        like_weighted_sims,
        watch_sims,
        rating_totals,
        like_totals,
        watch_totals,
        users,
    )


def _incoming(sims):
    reverse = defaultdict(dict)
    for anchor, row in sims.items():
        for neighbor, payload in row.items():
            reverse[neighbor][anchor] = payload
    return reverse


def combine_direct(
    target_ids,
    collection_ids,
    rating_sims,
    like_sims,
    like_weighted_sims,
    watch_sims,
    rating_totals,
    like_totals,
    watch_totals,
):
    """Blend local evidence into direct item-item edges.

    Ratings contribute residual-taste similarity; likes contribute both a
    Jaccard overlap (which penalizes "everyone likes it" pairs) and a
    user-weighted cosine.  Watches are a weak tie-breaker.  Collection
    membership is a structural franchise prior, not a behavior signal: it is
    only applied when the pair already has local behavioral support, so it
    cannot manufacture a cold metadata edge.

    Per-pair weights are renormalized when a signal has no co-occurrence
    support, so a film with likes but no ratings is not punished for missing
    the rating matrix.
    """
    reverse = {
        "rating": _incoming(rating_sims),
        "like": _incoming(like_sims),
        "like_weighted": _incoming(like_weighted_sims),
        "watch": _incoming(watch_sims),
    }
    rows = {}
    support_stats = Counter()
    for anchor in target_ids:
        candidates = set()
        for source, sims in (
            ("rating", rating_sims),
            ("like", like_sims),
            ("like_weighted", like_weighted_sims),
            ("watch", watch_sims),
        ):
            candidates.update(sims.get(anchor, {}))
            candidates.update(reverse[source].get(anchor, {}))
        candidates.discard(anchor)
        if not candidates:
            continue

        anchor_collection = collection_ids.get(anchor)
        scored = []
        for neighbor in candidates:
            parts = []
            co = {}
            rating_similarity = 0.0
            rating_jaccard = 0.0
            like_similarity = 0.0
            like_jaccard = 0.0
            like_weighted_similarity = 0.0
            watch_similarity = 0.0
            watch_jaccard = 0.0

            for name, sims in (
                ("rating", rating_sims),
                ("like", like_sims),
                ("like_weighted", like_weighted_sims),
                ("watch", watch_sims),
            ):
                forward = sims.get(anchor, {}).get(neighbor)
                backward = sims.get(neighbor, {}).get(anchor)
                best = forward
                if backward and (best is None or backward[0] > best[0]):
                    best = backward
                if best is None:
                    continue
                similarity, count = best
                co[name] = count
                if name == "rating":
                    rating_similarity = similarity
                    total = (
                        rating_totals.get(anchor, 0)
                        + rating_totals.get(neighbor, 0)
                        - count
                    )
                    rating_jaccard = count / total if total > 0 else 0.0
                    support_conf = count / (count + 4.0)
                    parts.append((RATING_WEIGHT, similarity))
                    parts.append(
                        (RATING_JACCARD_WEIGHT, rating_jaccard * support_conf)
                    )
                elif name == "like":
                    like_similarity = similarity
                    total = (
                        like_totals.get(anchor, 0)
                        + like_totals.get(neighbor, 0)
                        - count
                    )
                    like_jaccard = count / total if total > 0 else 0.0
                    # Jaccard is not shrunk by co-count; do that explicitly so
                    # two shared likes do not outrank fifty shared likes.
                    support_conf = count / (count + 4.0)
                    parts.append(
                        (LIKE_JACCARD_WEIGHT, like_jaccard * support_conf)
                    )
                elif name == "like_weighted":
                    like_weighted_similarity = similarity
                    # The weighted cosine has its own top-k list, so it can
                    # carry a real co-like edge even when the unweighted list
                    # did not.  Keep the support count for the reason text.
                    co["like"] = max(co.get("like", 0), count)
                    parts.append((LIKE_COSINE_WEIGHT, similarity))
                else:
                    watch_similarity = similarity
                    total = (
                        watch_totals.get(anchor, 0)
                        + watch_totals.get(neighbor, 0)
                        - count
                    )
                    watch_jaccard = count / total if total > 0 else 0.0
                    support_conf = count / (count + 4.0)
                    parts.append((WATCH_WEIGHT, watch_jaccard * support_conf))

            if not parts:
                continue
            weight_sum = sum(weight for weight, _ in parts)
            direct = sum(weight * similarity for weight, similarity in parts) / weight_sum
            # Structural franchise prior.  This is not a theme/keyword signal
            # and it cannot create an edge: it only nudges an edge that already
            # has local co-rating/co-like/co-watch support.
            collection_prior = 0.0
            if (
                anchor_collection
                and collection_ids.get(neighbor) == anchor_collection
                and direct > 0.03
            ):
                collection_prior = COLLECTION_PRIOR
                direct = min(1.0, direct + collection_prior)
            if direct <= 0.01:
                continue
            scored.append(
                (
                    direct,
                    neighbor,
                    {
                        "rating_similarity": round(rating_similarity, 4),
                        "rating_jaccard": round(rating_jaccard, 4),
                        "like_similarity": round(like_similarity, 4),
                        "like_jaccard": round(like_jaccard, 4),
                        "like_weighted_similarity": round(
                            like_weighted_similarity, 4
                        ),
                        "watch_similarity": round(watch_similarity, 4),
                        "watch_jaccard": round(watch_jaccard, 4),
                        "co_raters": co.get("rating", 0),
                        "co_likers": co.get("like", 0),
                        "co_watchers": co.get("watch", 0),
                        "collection_prior": round(collection_prior, 3),
                    },
                )
            )
        if not scored:
            continue
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        rows[anchor] = scored[:DIRECT_TOP_K]
        for _, _, evidence in rows[anchor]:
            support_stats["edges"] += 1
            if evidence["co_raters"]:
                support_stats["rating_edges"] += 1
            if evidence["co_likers"]:
                support_stats["like_edges"] += 1
            if evidence["co_watchers"]:
                support_stats["watch_edges"] += 1
            if evidence.get("collection_prior"):
                support_stats["collection_edges"] += 1
    return rows, support_stats


def build_adjacency(direct_rows):
    """Symmetrize direct edges so a path can be traversed in both directions."""
    adjacency = defaultdict(dict)
    for anchor, rows in direct_rows.items():
        for score, neighbor, _evidence in rows:
            current = adjacency[anchor].get(neighbor, 0.0)
            if score > current:
                adjacency[anchor][neighbor] = score
            current = adjacency[neighbor].get(anchor, 0.0)
            if score > current:
                adjacency[neighbor][anchor] = score
    return adjacency


def diffuse(adjacency, anchor, max_hops=3):
    """Truncated random-walk affinity around one anchor.

    Direct edges keep their score.  Two-hop paths are geometric means of the
    two edge weights, decayed by ``HOP2_DECAY``; three-hop paths use the same
    logic with a stronger decay.  Taking the strongest path (rather than
    summing every path) avoids inflating a candidate just because it is
    reachable through many popular intermediates.
    """
    direct = adjacency.get(anchor, {})
    best = {}
    for neighbor, score in direct.items():
        best[neighbor] = {
            "score": score,
            "hop": 1,
            "path_strength": score,
            "bridge": None,
        }

    if max_hops < 2 or not direct:
        return best

    top_direct = sorted(direct.items(), key=lambda item: item[1], reverse=True)[:40]
    two_hop = {}
    for bridge, first in top_direct:
        neighbours = adjacency.get(bridge, {})
        for neighbor, second in sorted(
            neighbours.items(), key=lambda item: item[1], reverse=True
        )[:12]:
            if neighbor == anchor:
                continue
            path_strength = math.sqrt(max(0.0, first * second))
            score = HOP2_DECAY * path_strength
            previous = two_hop.get(neighbor)
            if previous is None or score > previous[0]:
                two_hop[neighbor] = (score, bridge, path_strength)
    for neighbor, (score, bridge, path_strength) in two_hop.items():
        if neighbor not in best or score > best[neighbor]["score"]:
            best[neighbor] = {
                "score": score,
                "hop": 2,
                "path_strength": path_strength,
                "bridge": bridge,
            }

    if max_hops < 3 or not two_hop:
        return best

    top_two = sorted(two_hop.items(), key=lambda item: item[1][0], reverse=True)[:12]
    three_hop = {}
    for bridge, (_, _, first_path) in top_two:
        for neighbor, second in sorted(
            adjacency.get(bridge, {}).items(), key=lambda item: item[1], reverse=True
        )[:6]:
            if neighbor == anchor:
                continue
            path_strength = (max(0.0, first_path) * max(0.0, second)) ** (1.0 / 3.0)
            score = HOP3_DECAY * path_strength
            previous = three_hop.get(neighbor)
            if previous is None or score > previous[0]:
                three_hop[neighbor] = (score, bridge)
    for neighbor, (score, bridge) in three_hop.items():
        if neighbor not in best or score > best[neighbor]["score"]:
            best[neighbor] = {
                "score": score,
                "hop": 3,
                "path_strength": score / max(HOP3_DECAY, 1e-9),
                "bridge": bridge,
            }
    return best


def build_edges(
    target_ids,
    by_id,
    direct_rows,
    neighbors,
    max_hops,
    adjacency=None,
    direct_neighbors=DEFAULT_DIRECT_NEIGHBORS,
):
    if adjacency is None:
        adjacency = build_adjacency(direct_rows)
    edges = []
    hop_counts = Counter()
    films = 0
    scores = []
    for anchor in target_ids:
        best = diffuse(adjacency, anchor, max_hops=max_hops)
        if not best:
            continue
        ranked = sorted(
            best.items(),
            key=lambda item: (item[1]["score"], -item[1]["hop"], item[0]),
            reverse=True,
        )
        direct_selected = []
        indirect_selected = []
        direct = direct_rows.get(anchor, {})
        for neighbor, payload in ranked:
            if neighbor not in by_id:
                continue
            evidence = {
                "origin": "local_behavior",
                "hop": payload["hop"],
                "behavior_score": round(payload["score"], 4),
                "path_strength": round(payload["path_strength"], 4),
            }
            direct_evidence = None
            for score, candidate, candidate_evidence in direct:
                if candidate == neighbor:
                    direct_evidence = candidate_evidence
                    break
            if direct_evidence:
                evidence.update(direct_evidence)
            if payload["bridge"] is not None:
                evidence["bridge_tmdb_id"] = payload["bridge"]
            item = (round(payload["score"], 6), neighbor, evidence)
            if payload["hop"] == 1:
                direct_selected.append(item)
            else:
                indirect_selected.append(item)

        # Direct local evidence owns the shelf ranks.  Two/three-hop
        # exploration fills the deeper ranks, so a longer walk can never push
        # out a direct co-rating/co-like edge from the visible shelf.
        direct_limit = min(len(direct_selected), max(0, min(direct_neighbors, neighbors)))
        selected = direct_selected[:direct_limit]
        remaining = neighbors - len(selected)
        if remaining > 0:
            selected.extend(indirect_selected[:remaining])
        if not selected:
            continue
        for rank, (score, neighbor, evidence) in enumerate(selected, 1):
            edges.append(
                (
                    anchor,
                    neighbor,
                    score,
                    rank,
                    "behavior_v1",
                    json.dumps({"signals": evidence}, ensure_ascii=False),
                )
            )
            hop_counts[evidence["hop"]] += 1
            scores.append(score)
        films += 1
    return edges, hop_counts, films, scores


def build_behavior_subniches(
    target_ids,
    by_id,
    adjacency,
    *,
    min_edge=0.22,
    per_node=10,
    resolution=1.0,
    min_size=8,
):
    """Find local taste communities in the behavior graph.

    This is Louvain community detection over *strong* behavior edges only.
    Unlike an NMF over the raw user matrix, a community is a set of films that
    local viewers actually connect to one another; the label is built from the
    community's own most central films, not from Letterboxd themes or TMDB
    keywords.
    """
    try:
        import networkx as nx
    except ImportError:
        return [], {}

    graph = nx.Graph()
    for anchor in target_ids:
        row = adjacency.get(anchor, {})
        for neighbor, score in sorted(
            row.items(), key=lambda item: item[1], reverse=True
        )[:per_node]:
            if neighbor == anchor or neighbor not in by_id or score < min_edge:
                continue
            existing = graph.get_edge_data(anchor, neighbor)
            if existing is None or score > existing["weight"]:
                graph.add_edge(anchor, neighbor, weight=float(score))
    if graph.number_of_nodes() < min_size:
        return [], {}

    communities = nx.algorithms.community.louvain_communities(
        graph,
        weight="weight",
        resolution=resolution,
        seed=42,
    )
    communities = [set(community) for community in communities]
    communities = [community for community in communities if len(community) >= min_size]
    communities.sort(key=len, reverse=True)

    clusters = []
    memberships = {}
    for index, nodes in enumerate(communities):
        internal_strength = {
            node: sum(
                graph[node][neighbor]["weight"]
                for neighbor in graph[node]
                if neighbor in nodes
            )
            for node in nodes
        }
        top = sorted(
            internal_strength,
            key=lambda film: (internal_strength[film], film),
            reverse=True,
        )[:6]
        label_titles = [
            by_id[film]["title"] for film in top[:2] if film in by_id
        ]
        label = (
            "Viewers of " + " · ".join(label_titles)
            if label_titles
            else f"Local behavior niche {index + 1:02d}"
        )
        clusters.append(
            {
                "component": index,
                "slug": f"behavior-niche-{index + 1:02d}",
                "label": label,
                "top_films": top,
                "size": len(nodes),
            }
        )
        for film in nodes:
            memberships[film] = {index: 1.0}
    return clusters, memberships


def ensure_tables(con):
    con.execute(
        """
        create table if not exists behavior_clusters (
            id integer primary key autoincrement,
            slug varchar not null unique,
            label varchar not null,
            size integer not null default 0,
            top_films text,
            created_at datetime
        )
        """
    )
    con.execute(
        """
        create table if not exists film_behavior_subniches (
            id integer primary key autoincrement,
            tmdb_id integer not null,
            cluster_id integer not null,
            weight float not null default 0,
            created_at datetime,
            unique(tmdb_id, cluster_id)
        )
        """
    )
    con.execute(
        "create index if not exists ix_film_behavior_subniches_tmdb "
        "on film_behavior_subniches(tmdb_id)"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="top Letterboxd ranks for a pilot")
    parser.add_argument("--neighbors", type=int, default=DEFAULT_NEIGHBORS)
    parser.add_argument(
        "--direct-neighbors",
        type=int,
        default=DEFAULT_DIRECT_NEIGHBORS,
        help="direct behavior edges kept before multi-hop expansion edges",
    )
    parser.add_argument("--max-hops", type=int, default=3)
    parser.add_argument("--subniche-min-edge", type=float, default=0.22)
    parser.add_argument("--subniche-resolution", type=float, default=1.0)
    parser.add_argument("--subniche-per-node", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live and not args.dry_run:
        print("refusing live DB without --allow-live", file=sys.stderr)
        return 2
    if args.max_hops < 1 or args.max_hops > 3:
        print("--max-hops must be 1..3", file=sys.stderr)
        return 2

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = load_catalog(con, args.limit)
    if not rows:
        print("no films found", file=sys.stderr)
        return 2
    target_ids = {int(row["tmdb_id"]) for row in rows}
    by_id = {int(row["tmdb_id"]): row for row in rows}
    collection_ids = {
        int(row["tmdb_id"]): int(row["collection_id"])
        for row in rows
        if row.get("collection_id")
    }
    print(
        json.dumps(
            {
                "db": str(db_path),
                "films": len(rows),
                "limit": args.limit,
                "max_hops": args.max_hops,
            },
            indent=2,
        ),
        flush=True,
    )

    started = time.monotonic()
    ratings, likes, watches = load_interactions(con, target_ids)
    users = sorted(set(ratings) | set(likes) | set(watches))
    print(
        json.dumps(
            {
                "ratings_users": len(ratings),
                "likes_users": len(likes),
                "watch_users": len(watches),
                "users": len(users),
                "rating_rows": sum(len(items) for items in ratings.values()),
                "like_rows": sum(len(items) for items in likes.values()),
                "watch_rows": sum(len(items) for items in watches.values()),
            },
            indent=2,
        ),
        flush=True,
    )
    if len(users) < 2:
        print("not enough local behavior to build a graph", file=sys.stderr)
        return 2

    (
        rating_sims,
        like_sims,
        like_weighted_sims,
        watch_sims,
        rating_totals,
        like_totals,
        watch_totals,
        users,
    ) = build_similarities(target_ids, ratings, likes, watches)
    print(
        json.dumps(
            {
                "rating_films": len(rating_sims),
                "like_films": len(like_sims),
                "like_weighted_films": len(like_weighted_sims),
                "watch_films": len(watch_sims),
            },
            indent=2,
        ),
        flush=True,
    )
    direct_rows, support_stats = combine_direct(
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
    print(
        json.dumps(
            {
                "direct_anchors": len(direct_rows),
                "direct_edges": support_stats["edges"],
                "rating_edges": support_stats["rating_edges"],
                "like_edges": support_stats["like_edges"],
                "watch_edges": support_stats["watch_edges"],
                "collection_edges": support_stats["collection_edges"],
            },
            indent=2,
        ),
        flush=True,
    )

    adjacency = build_adjacency(direct_rows)
    edges, hop_counts, films_with_edges, scores = build_edges(
        target_ids,
        by_id,
        direct_rows,
        args.neighbors,
        args.max_hops,
        adjacency=adjacency,
        direct_neighbors=args.direct_neighbors,
    )
    print(
        json.dumps(
            {
                "behavior_edges": len(edges),
                "films_with_edges": films_with_edges,
                "hops": dict(hop_counts),
                "average_score": round(float(np.mean(scores)), 4) if scores else 0,
            },
            indent=2,
        ),
        flush=True,
    )

    clusters, memberships = build_behavior_subniches(
        target_ids,
        by_id,
        adjacency,
        min_edge=args.subniche_min_edge,
        per_node=args.subniche_per_node,
        resolution=args.subniche_resolution,
    )
    print(
        json.dumps(
            {
                "behavior_clusters": len(clusters),
                "behavior_members": len(memberships),
            },
            indent=2,
        ),
        flush=True,
    )

    sample_titles = {
        "the dark knight",
        "the dark knight rises",
        "batman begins",
        "parasite",
        "interstellar",
        "the substance",
        "everything everywhere all at once",
        "spirited away",
        "heat",
    }
    samples = {}
    for _, neighbor, score, rank, source, evidence_raw in edges:
        anchor = by_id.get(_)
        if not anchor or str(anchor.get("title", "")).lower() not in sample_titles:
            continue
        if rank > 8:
            continue
        entry = {
            "tmdb_id": neighbor,
            "title": by_id[neighbor].get("title"),
            "score": score,
            "signals": json.loads(evidence_raw)["signals"],
        }
        samples.setdefault(anchor["title"], []).append(entry)

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "films": len(rows),
        "limit": args.limit,
        "max_hops": args.max_hops,
        "users": len(users),
        "rating_rows": sum(len(items) for items in ratings.values()),
        "like_rows": sum(len(items) for items in likes.values()),
        "watch_rows": sum(len(items) for items in watches.values()),
        "direct_anchors": len(direct_rows),
        "direct_edges": support_stats["edges"],
        "rating_edges": support_stats["rating_edges"],
        "like_edges": support_stats["like_edges"],
        "watch_edges": support_stats["watch_edges"],
        "collection_edges": support_stats["collection_edges"],
        "behavior_edges": len(edges),
        "films_with_edges": films_with_edges,
        "hop_counts": dict(hop_counts),
        "average_score": round(float(np.mean(scores)), 4) if scores else 0,
        "behavior_clusters": [
            {key: value for key, value in cluster.items() if key != "component"}
            for cluster in clusters
        ],
        "behavior_members": len(memberships),
        "samples": samples,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    out_path = args.report or (
        BACKEND
        / "reports"
        / f"behavior_graph_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    if not args.dry_run:
        now = datetime.now(timezone.utc).isoformat()
        ensure_tables(con)
        con.execute("delete from film_neighbors where source = 'behavior_v1'")
        con.execute("delete from film_behavior_subniches")
        con.execute("delete from behavior_clusters")
        cluster_ids = {}
        for cluster in clusters:
            cursor = con.execute(
                "insert into behavior_clusters (slug, label, size, top_films, created_at) "
                "values (?, ?, ?, ?, ?)",
                (
                    cluster["slug"],
                    cluster["label"],
                    cluster["size"],
                    json.dumps(cluster["top_films"], ensure_ascii=False),
                    now,
                ),
            )
            cluster_ids[cluster["component"]] = cursor.lastrowid
        membership_rows = []
        for film, weights in memberships.items():
            for component, weight in weights.items():
                if component in cluster_ids:
                    membership_rows.append((film, cluster_ids[component], weight, now))
        if membership_rows:
            con.executemany(
                "insert into film_behavior_subniches (tmdb_id, cluster_id, weight, created_at) "
                "values (?, ?, ?, ?)",
                membership_rows,
            )
        if edges:
            con.executemany(
                "insert into film_neighbors (tmdb_id, neighbor_tmdb_id, score, rank, source, evidence, created_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                [(*edge, now) for edge in edges],
            )
        con.commit()
        report["written"] = {
            "behavior_clusters": len(clusters),
            "film_behavior_subniches": len(membership_rows),
            "film_neighbors": len(edges),
        }
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report: {out_path}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
