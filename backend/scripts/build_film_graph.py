"""Build derived subniches and the local ``more like this`` graph.

The catalog is now the runtime source of truth.  This script turns catalog
evidence into two persisted artifacts:

* ``theme_clusters`` / ``film_subniches`` — soft, data-derived groups of
  Letterboxd themes (not hand-written genres), with a label from the themes
  that distinguish each group.
* ``film_neighbors`` — top-K directional recommendation edges per film, scored
  from collection/franchise membership, director/writer/cast overlap, theme
  and subniche overlap, TMDB related edges, and embedding similarity.

The full build is deliberately restart-safe at the film level and refuses the
live DB by default.  Use ``--limit 2000`` against a copy first (the pilot),
inspect the report, then run the full catalog.

Usage:
    cd backend && ../.venv/bin/python scripts/build_film_graph.py \
        --db /tmp/opencode/cinematch_pilot.db --limit 2000 --dry-run
    ... --db cinematch.db --allow-live
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
sys.path.insert(0, str(BACKEND))


def norm_label(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def norm_title(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


def parse_json(value, default):
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_theme_sets(rows):
    """Return all theme ids plus a Letterboxd-only view for subniches.

    Keyword/genre fallbacks keep the graph usable for films whose theme page is
    still missing, but the derived subniches should describe actual Letterboxd
    theme vocabulary, not TMDB keywords.
    """
    theme_sets: dict[int, set[str]] = {}
    letterboxd_sets: dict[int, set[str]] = {}
    theme_labels: dict[str, str] = {}
    theme_counts: Counter = Counter()
    letterboxd_counts: Counter = Counter()
    for row in rows:
        themes = parse_json(row.get("themes"), [])
        labels = set()
        letterboxd_labels = set()
        for item in themes:
            if isinstance(item, str):
                label = item
                source = None
            elif isinstance(item, dict):
                label = item.get("label") or item.get("id") or item.get("slug")
                source = item.get("source")
            else:
                label = None
                source = None
            if not label:
                continue
            key = norm_label(label)
            if not key:
                continue
            labels.add(key)
            theme_labels.setdefault(key, label.strip())
            if source in (None, "letterboxd", "letterboxd_dataset", "letterboxd_page"):
                letterboxd_labels.add(key)
        if labels:
            theme_sets[int(row["tmdb_id"])] = labels
            theme_counts.update(labels)
        if letterboxd_labels:
            letterboxd_sets[int(row["tmdb_id"])] = letterboxd_labels
            letterboxd_counts.update(letterboxd_labels)
    return theme_sets, letterboxd_sets, theme_labels, theme_counts, letterboxd_counts


def build_subniches(rows, theme_sets, theme_labels, theme_counts, k: int):
    """Fit a soft theme-topic model with NMF and return clusters + memberships."""
    from scipy import sparse
    from sklearn.decomposition import NMF
    from sklearn.feature_extraction.text import TfidfTransformer

    vocab = sorted(
        theme
        for theme, count in theme_counts.items()
        if count >= 2 and count <= max(20, int(len(rows) * 0.40))
    )
    if len(vocab) < 4:
        return [], {}, {}
    index = {theme: i for i, theme in enumerate(vocab)}
    film_ids = []
    matrix_rows = []
    for row in rows:
        tmdb_id = int(row["tmdb_id"])
        themes = theme_sets.get(tmdb_id)
        if not themes:
            continue
        cols = sorted(index[t] for t in themes if t in index)
        if not cols:
            continue
        film_ids.append(tmdb_id)
        matrix_rows.append(cols)
    k = min(k, max(2, len(vocab) - 1), max(2, len(film_ids) - 1))
    if len(film_ids) < k * 3:
        k = max(2, min(k, max(2, len(film_ids) // 3)))
    if len(film_ids) < 4 or k < 2:
        return [], {}, {}

    indptr = [0]
    indices = []
    for cols in matrix_rows:
        indices.extend(cols)
        indptr.append(len(indices))
    data = np.ones(len(indices), dtype=np.float32)
    X = sparse.csr_matrix(
        (data, indices, np.asarray(indptr, dtype=np.int32)),
        shape=(len(film_ids), len(vocab)),
    )
    X = TfidfTransformer(norm="l2").fit_transform(X)
    model = NMF(
        n_components=k,
        init="nndsvda",
        random_state=42,
        max_iter=350,
        beta_loss="frobenius",
    )
    W = model.fit_transform(X)
    H = model.components_
    W = W / np.clip(W.sum(axis=1, keepdims=True), 1e-9, None)
    H = H / np.clip(H.sum(axis=1, keepdims=True), 1e-9, None)

    clusters = []
    for component in range(k):
        top_indices = np.argsort(-H[component])[:10]
        themes = [vocab[i] for i in top_indices if H[component][i] > 0.005]
        if not themes:
            continue
        # Prefer distinctive themes for the human label: high component weight
        # and lower catalog frequency.  The full list stays on the cluster.
        label_order = sorted(
            themes,
            key=lambda t: (H[component][index[t]] / math.sqrt(max(theme_counts[t], 1))),
            reverse=True,
        )
        labels = [theme_labels.get(t, t) for t in label_order[:3]]
        label = " · ".join(labels)
        slug = f"subniche-{component + 1:02d}-{norm_label(label_order[0])[:40]}"
        clusters.append(
            {
                "component": component,
                "slug": slug,
                "label": label,
                "themes": themes,
                "theme_labels": [theme_labels.get(t, t) for t in themes],
                "size": int((W[:, component] >= 0.10).sum()),
            }
        )

    memberships: dict[int, dict[int, float]] = {}
    for row_index, tmdb_id in enumerate(film_ids):
        weights = W[row_index]
        order = np.argsort(-weights)[:3]
        chosen = {int(c): float(weights[c]) for c in order if weights[c] >= 0.08}
        if not chosen:
            chosen = {int(order[0]): float(weights[order[0]])}
        total = sum(chosen.values()) or 1.0
        memberships[tmdb_id] = {c: round(v / total, 4) for c, v in chosen.items()}
    return clusters, memberships, index


def load_people(con, target_ids):
    directors = defaultdict(set)
    writers = defaultdict(set)
    cast = defaultdict(list)
    query = (
        "select tmdb_id, person_id, role, ordering from film_people "
        "where role in ('director','writer','cast')"
    )
    for tmdb_id, person_id, role, ordering in con.execute(query):
        tmdb_id = int(tmdb_id)
        if tmdb_id not in target_ids:
            continue
        if role == "director":
            directors[tmdb_id].add(int(person_id))
        elif role == "writer":
            writers[tmdb_id].add(int(person_id))
        elif role == "cast":
            cast[tmdb_id].append((ordering if ordering is not None else 999, int(person_id)))
    for tmdb_id in cast:
        cast[tmdb_id] = [person for _, person in sorted(cast[tmdb_id])[:12]]
    return directors, writers, cast


def load_related(con, target_ids):
    direct = defaultdict(set)
    reverse = defaultdict(set)
    for tmdb_id, related_id, source in con.execute(
        "select tmdb_id, related_tmdb_id, source from film_related"
    ):
        tmdb_id = int(tmdb_id)
        related_id = int(related_id)
        if tmdb_id in target_ids and related_id in target_ids:
            direct[tmdb_id].add(related_id)
            reverse[related_id].add(tmdb_id)
    return direct, reverse


def build_embedding_neighbors(ids, embeddings, top_k=60, chunk_size=512):
    """Cosine top-K for each film, computed in bounded-memory row chunks."""
    order = [tmdb_id for tmdb_id in ids if embeddings.get(tmdb_id) is not None]
    if not order:
        return {}
    dim = len(next(iter(embeddings.values())))
    matrix = np.zeros((len(order), dim), dtype=np.float32)
    for i, tmdb_id in enumerate(order):
        vec = embeddings[tmdb_id]
        if len(vec) == dim:
            matrix[i] = vec
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.clip(norms, 1e-9, None)
    result: dict[int, list[tuple[int, float]]] = {}
    k = min(top_k, max(1, len(order) - 1))
    for start in range(0, len(order), chunk_size):
        end = min(start + chunk_size, len(order))
        block = matrix[start:end] @ matrix.T
        for local, row in enumerate(block):
            anchor_id = order[start + local]
            row = row.copy()
            row[start + local] = -2.0
            if k < row.size:
                top = np.argpartition(-row, k)[:k]
            else:
                top = np.argsort(-row)
            result[anchor_id] = [
                (order[i], float(row[i]))
                for i in top
                if row[i] > 0.01
            ]
    return result


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def weighted_cast_similarity(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    aw = {person: 1.0 / (1.0 + index) for index, person in enumerate(a[:10])}
    bw = {person: 1.0 / (1.0 + index) for index, person in enumerate(b[:10])}
    shared = set(aw) & set(bw)
    if not shared:
        return 0.0
    return sum(min(aw[person], bw[person]) for person in shared) / max(
        sum(aw.values()), 1e-9
    )


def quality_prior(vote_average, vote_count) -> float:
    rating = min(1.0, max(0.0, (vote_average or 0.0) / 10.0))
    count = math.log1p(max(vote_count or 0, 0)) / math.log1p(10000.0)
    return 0.65 * rating + 0.35 * min(1.0, count)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="top Letterboxd ranks for a pilot")
    parser.add_argument("--neighbors", type=int, default=24)
    parser.add_argument("--clusters", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live and not args.dry_run:
        print("refusing live DB without --allow-live", file=sys.stderr)
        return 2
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    if args.limit:
        rows = [
            dict(row)
            for row in con.execute(
                """
                select m.tmdb_id, m.title, m.release_year, m.genres, m.collection_id,
                       m.themes, m.keywords, m.embedding, m.vote_average, m.vote_count,
                       m.popularity, p.rank
                  from movie_cache m
                  join letterboxd_popularity p on p.tmdb_id = m.tmdb_id
                 where p.rank <= ?
                 order by p.rank
                """,
                (args.limit,),
            )
        ]
    else:
        rows = [
            dict(row)
            for row in con.execute(
                """
                select m.tmdb_id, m.title, m.release_year, m.genres, m.collection_id,
                       m.themes, m.keywords, m.embedding, m.vote_average, m.vote_count,
                       m.popularity
                  from movie_cache m
                """
            )
        ]
    if not rows:
        print("no films found", file=sys.stderr)
        return 2
    target_ids = {int(row["tmdb_id"]) for row in rows}
    film_order = [int(row["tmdb_id"]) for row in rows]
    by_id = {int(row["tmdb_id"]): row for row in rows}
    print(json.dumps({"db": str(db_path), "films": len(rows), "limit": args.limit}, indent=2), flush=True)

    # Scalar features.
    genre_sets = {}
    collection_films = defaultdict(list)
    era_genre = defaultdict(list)
    embeddings = {}
    for row in rows:
        tmdb_id = int(row["tmdb_id"])
        genres = {
            g.strip().lower() for g in (row.get("genres") or "").split(",") if g.strip()
        }
        genre_sets[tmdb_id] = genres
        if row.get("collection_id"):
            collection_films[int(row["collection_id"])].append(tmdb_id)
        year = row.get("release_year")
        if year:
            bucket = int(year) // 5
            for genre in genres:
                era_genre[(genre, bucket)].append(tmdb_id)
        vec = parse_json(row.get("embedding"), None)
        if isinstance(vec, list) and vec:
            embeddings[tmdb_id] = np.asarray(vec, dtype=np.float32)

    theme_sets, letterboxd_sets, theme_labels, theme_counts, letterboxd_counts = load_theme_sets(rows)
    directors, writers, cast = load_people(con, target_ids)
    direct_related, reverse_related = load_related(con, target_ids)

    clusters, memberships, theme_vocab = build_subniches(
        rows, letterboxd_sets, theme_labels, letterboxd_counts, args.clusters
    )
    print(
        json.dumps(
            {
                "theme_films": len(theme_sets),
                "letterboxd_theme_films": len(letterboxd_sets),
                "theme_vocabulary": len(theme_vocab),
                "clusters": len(clusters),
                "cluster_members": len(memberships),
                "embedded": len(embeddings),
            },
            indent=2,
        ),
        flush=True,
    )

    # Inverted indices for candidate generation.  Sorting by popularity keeps
    # the caps biased toward recognizable films without excluding deep cuts.
    pop = {int(row["tmdb_id"]): float(row.get("popularity") or 0.0) for row in rows}
    director_films = defaultdict(list)
    writer_films = defaultdict(list)
    cast_films = defaultdict(list)
    theme_films = defaultdict(list)
    for tmdb_id in target_ids:
        for person in directors.get(tmdb_id, ()):
            director_films[person].append(tmdb_id)
        for person in writers.get(tmdb_id, ()):
            writer_films[person].append(tmdb_id)
        for person in cast.get(tmdb_id, ())[:6]:
            cast_films[person].append(tmdb_id)
        for theme in theme_sets.get(tmdb_id, ()):
            theme_films[theme].append(tmdb_id)
    for index in (director_films, writer_films, cast_films, theme_films):
        for key in index:
            index[key] = sorted(set(index[key]), key=lambda tid: pop.get(tid, 0.0), reverse=True)

    embedding_neighbors = build_embedding_neighbors(list(target_ids), embeddings, top_k=60)
    print(json.dumps({"embedding_neighbors": len(embedding_neighbors)}, indent=2), flush=True)

    # Optional cluster co-membership index, used as a small graph signal.
    cluster_films = defaultdict(list)
    for tmdb_id, weights in memberships.items():
        for component in weights:
            cluster_films[component].append(tmdb_id)
    for component in cluster_films:
        cluster_films[component] = sorted(
            set(cluster_films[component]), key=lambda tid: pop.get(tid, 0.0), reverse=True
        )

    started = time.monotonic()
    all_edges = []
    stats = Counter()
    samples = {}
    sample_titles = {
        "dark knight", "dark knight rises", "batman begins", "interstellar",
        "parasite", "the substance", "everything everywhere all at once",
        "spirited away", "heat",
    }
    for index, anchor_id in enumerate(film_order, 1):
        anchor = by_id[anchor_id]
        candidates: dict[int, float] = {}

        def add(candidate_id: int, priority: float) -> None:
            if candidate_id == anchor_id or candidate_id not in target_ids:
                return
            current = candidates.get(candidate_id)
            if current is None or priority > current:
                candidates[candidate_id] = priority

        for related_id in direct_related.get(anchor_id, ()):
            add(related_id, 100.0)
        for related_id in list(reverse_related.get(anchor_id, ()))[:20]:
            add(related_id, 92.0)
        for collection_id in [anchor.get("collection_id")]:
            if collection_id:
                for other in collection_films.get(int(collection_id), ())[:20]:
                    add(other, 88.0)
        for person in directors.get(anchor_id, ()):
            for other in director_films.get(person, ())[:35]:
                add(other, 82.0)
        for theme in sorted(
            theme_sets.get(anchor_id, ()),
            key=lambda t: theme_counts.get(t, 0),
        )[:5]:
            for other in theme_films.get(theme, ())[:35]:
                add(other, 74.0)
        for person in cast.get(anchor_id, ())[:5]:
            for other in cast_films.get(person, ())[:30]:
                add(other, 68.0)
        for person in writers.get(anchor_id, ()):
            for other in writer_films.get(person, ())[:25]:
                add(other, 62.0)
        for component in memberships.get(anchor_id, {}):
            for other in cluster_films.get(component, ())[:35]:
                add(other, 55.0)
        for related_id, similarity in embedding_neighbors.get(anchor_id, ())[:45]:
            add(related_id, 50.0 + similarity)
        # A weak genre/era fallback helps films with sparse credits/themes.
        anchor_year = anchor.get("release_year")
        if anchor_year:
            bucket = int(anchor_year) // 5
            for genre in list(genre_sets.get(anchor_id, ()))[:2]:
                for other_id in era_genre.get((genre, bucket), ())[:25]:
                    add(other_id, 10.0)

        scored = []
        a_genres = genre_sets.get(anchor_id, set())
        a_themes = theme_sets.get(anchor_id, set())
        a_sub = set(memberships.get(anchor_id, {}))
        a_collection = anchor.get("collection_id")
        for candidate_id, _priority in candidates.items():
            other = by_id[candidate_id]
            b_genres = genre_sets.get(candidate_id, set())
            b_themes = theme_sets.get(candidate_id, set())
            b_sub = set(memberships.get(candidate_id, {}))
            theme_sim = jaccard(a_themes, b_themes)
            genre_sim = jaccard(a_genres, b_genres)
            director_sim = jaccard(directors.get(anchor_id, set()), directors.get(candidate_id, set()))
            writer_sim = jaccard(writers.get(anchor_id, set()), writers.get(candidate_id, set()))
            cast_sim = weighted_cast_similarity(cast.get(anchor_id, []), cast.get(candidate_id, []))
            sub_sim = jaccard(a_sub, b_sub)
            collection_sim = 1.0 if a_collection and a_collection == other.get("collection_id") else 0.0
            if candidate_id in direct_related.get(anchor_id, ()):
                related_sim = 1.0
            elif candidate_id in reverse_related.get(anchor_id, ()):
                related_sim = 0.55
            else:
                related_sim = 0.0
            emb_sim = 0.0
            if anchor_id in embeddings and candidate_id in embeddings:
                denom = float(np.linalg.norm(embeddings[anchor_id]) * np.linalg.norm(embeddings[candidate_id]))
                if denom > 0:
                    emb_sim = max(0.0, float(np.dot(embeddings[anchor_id], embeddings[candidate_id]) / denom))
            quality = quality_prior(other.get("vote_average"), other.get("vote_count"))
            anchor_year = anchor.get("release_year")
            other_year = other.get("release_year")
            era = 0.5
            if anchor_year and other_year:
                era = max(0.0, 1.0 - abs(anchor_year - other_year) / 40.0)
            total = (
                0.20 * collection_sim
                + 0.16 * director_sim
                + 0.06 * writer_sim
                + 0.10 * cast_sim
                + 0.20 * theme_sim
                + 0.07 * sub_sim
                + 0.10 * related_sim
                + 0.26 * emb_sim
                + 0.03 * genre_sim
                + 0.02 * quality
                + 0.02 * era
            ) / 1.22
            if total <= 0.01:
                continue
            scored.append(
                (
                    round(total, 6),
                    candidate_id,
                    {
                        "embedding": round(emb_sim, 4),
                        "theme": round(theme_sim, 4),
                        "collection": round(collection_sim, 3),
                        "director": round(director_sim, 3),
                        "writer": round(writer_sim, 3),
                        "cast": round(cast_sim, 3),
                        "subniche": round(sub_sim, 3),
                        "related": round(related_sim, 3),
                        "genre": round(genre_sim, 3),
                        "quality": round(quality, 3),
                        "era": round(era, 3),
                    },
                )
            )

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = []
        counts = Counter()
        selected_vectors = []
        for score, candidate_id, evidence in scored:
            collection_id = by_id[candidate_id].get("collection_id")
            collection_key = int(collection_id) if collection_id else None
            if collection_key and counts[("collection", collection_key)] >= 4:
                continue
            director_key = None
            shared_directors = directors.get(anchor_id, set()) & directors.get(candidate_id, set())
            if shared_directors:
                director_key = min(shared_directors)
            if director_key and counts[("director", director_key)] >= 5:
                continue
            if (
                evidence["collection"] == 0
                and candidate_id not in direct_related.get(anchor_id, ())
                and embeddings.get(candidate_id) is not None
            ):
                candidate_vec = embeddings[candidate_id]
                if any(
                    float(
                        np.dot(candidate_vec, selected_vec)
                        / max(1e-9, float(np.linalg.norm(candidate_vec) * np.linalg.norm(selected_vec)))
                    )
                    > 0.965
                    for selected_vec in selected_vectors
                ):
                    continue
            selected.append((score, candidate_id, evidence))
            if collection_key:
                counts[("collection", collection_key)] += 1
            if director_key:
                counts[("director", director_key)] += 1
            if embeddings.get(candidate_id) is not None:
                selected_vectors.append(embeddings[candidate_id])
            if len(selected) >= args.neighbors:
                break

        for rank, (score, candidate_id, evidence) in enumerate(selected, 1):
            all_edges.append(
                (
                    anchor_id,
                    candidate_id,
                    score,
                    rank,
                    "hybrid_v1",
                    json.dumps({"signals": evidence}, ensure_ascii=False),
                )
            )
        stats["films_with_neighbors"] += 1 if selected else 0
        stats["edges"] += len(selected)
        stats["score_sum"] += sum(item[0] for item in selected)
        if selected:
            stats["collection_edges"] += sum(1 for item in selected if item[2]["collection"] > 0)
            stats["theme_edges"] += sum(1 for item in selected if item[2]["theme"] > 0)
            stats["related_edges"] += sum(1 for item in selected if item[2]["related"] > 0)
        if norm_title(anchor.get("title")) in sample_titles:
            samples[anchor.get("title")] = [
                {
                    "tmdb_id": candidate_id,
                    "title": by_id[candidate_id].get("title"),
                    "score": score,
                    "signals": evidence,
                }
                for score, candidate_id, evidence in selected[:8]
            ]
        if index % 500 == 0 or index == len(target_ids):
            elapsed = max(0.001, time.monotonic() - started)
            print(
                f"  graph {index}/{len(target_ids)} films edges={stats['edges']} rate={index/elapsed:.1f}/s",
                flush=True,
            )

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "films": len(rows),
        "limit": args.limit,
        "clusters": [
            {key: value for key, value in cluster.items() if key != "component"}
            for cluster in clusters
        ],
        "cluster_members": len(memberships),
        "neighbors_per_film": args.neighbors,
        "edges": len(all_edges),
        "films_with_neighbors": stats["films_with_neighbors"],
        "average_score": round(stats["score_sum"] / max(stats["edges"], 1), 4),
        "collection_edge_share": round(stats["collection_edges"] / max(stats["edges"], 1), 3),
        "theme_edge_share": round(stats["theme_edges"] / max(stats["edges"], 1), 3),
        "related_edge_share": round(stats["related_edges"] / max(stats["edges"], 1), 3),
        "samples": samples,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    out_path = args.report or (
        BACKEND / "reports" / f"film_graph_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    if not args.dry_run:
        now = datetime.now(timezone.utc).isoformat()
        con.execute("delete from film_neighbors where source = 'hybrid_v1'")
        con.execute("delete from film_subniches")
        con.execute("delete from theme_clusters")
        cluster_ids = {}
        for component, cluster in enumerate(clusters):
            cursor = con.execute(
                "insert into theme_clusters (slug, label, parent_id, level, size, themes, created_at) "
                "values (?, ?, null, 'subniche', ?, ?, ?)",
                (
                    cluster["slug"],
                    cluster["label"],
                    cluster["size"],
                    json.dumps(cluster["themes"], ensure_ascii=False),
                    now,
                ),
            )
            cluster_ids[component] = cursor.lastrowid
        membership_rows = []
        for tmdb_id, weights in memberships.items():
            for component, weight in weights.items():
                if component in cluster_ids:
                    membership_rows.append((tmdb_id, cluster_ids[component], weight, now))
        if membership_rows:
            con.executemany(
                "insert into film_subniches (tmdb_id, cluster_id, weight, created_at) "
                "values (?, ?, ?, ?)",
                membership_rows,
            )
        if all_edges:
            con.executemany(
                "insert into film_neighbors (tmdb_id, neighbor_tmdb_id, score, rank, source, evidence, created_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                [(*edge, now) for edge in all_edges],
            )
        con.commit()
        report["written"] = {
            "theme_clusters": len(clusters),
            "film_subniches": len(membership_rows),
            "film_neighbors": len(all_edges),
        }
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report: {out_path}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
