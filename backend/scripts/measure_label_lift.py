"""Measure per-label predictive lift from the seed cohort.

Not every niche label deserves equal trust: Letterboxd themes are curated,
TMDB keyword fallbacks are noisy, and the derived subniches vary in size.
This script measures each label against the one ground truth we have — what
the cohort actually positively rated or liked — instead of trusting the
vocabulary:

    lift(label) = P(positive | interaction on a film carrying the label)
                / P(positive | interaction on any rated film)

An interaction is a seed-user star rating (positive at ``--min-rating``) or a
like on a film the user never rated. Support is the interaction count behind
the estimate; labels below ``--min-support`` are reported but excluded from
the rankings, because low-support lift is noise.

The output feeds decisions, not scoring: promote labels whose lift holds up,
demote or merge the ones below base rate. Ranking-side weighting of labels is
a later, separately gated change.

Usage:
    cd backend && ../.venv/bin/python scripts/measure_label_lift.py \
        --db cinematch.db --out reports/label_lift.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()

SUBNICHE_MIN_WEIGHT = 0.15  # keep in sync with services.features


def _load_interactions(con: sqlite3.Connection, min_rating: float):
    """Return (film -> [positive, ...]) interaction lists and totals."""
    positives: dict[int, list[int]] = defaultdict(list)

    cohort = {
        int(row[0])
        for row in con.execute(
            "select user_id from users where is_seed = 1 or username = 'mustafa'"
        )
    }
    if not cohort:
        return positives, {
            "rated_interactions": 0,
            "liked_unrated_interactions": 0,
            "total_interactions": 0,
            "positive_interactions": 0,
            "base_rate": 0.0,
        }
    placeholders = ",".join("?" for _ in cohort)

    rows = con.execute(
        f"select tmdb_id, rating from logged_movies where user_id in ({placeholders})",
        tuple(cohort),
    ).fetchall()
    rated = 0
    rated_by_user: set[tuple[int, int]] = set()
    for tmdb_id, rating in rows:
        if tmdb_id is None:
            continue
        rated += 1
        positives[int(tmdb_id)].append(1 if float(rating) >= min_rating else 0)

    # Attach user ids for per-user like dedupe: a liked film the user also
    # rated adds no second interaction; the rating is the evidence.
    rated_by_user = {
        (int(user_id), int(tmdb_id))
        for user_id, tmdb_id in con.execute(
            f"select user_id, tmdb_id from logged_movies where user_id in ({placeholders})",
            tuple(cohort),
        )
        if tmdb_id is not None
    }
    like_rows = con.execute(
        f"select user_id, tmdb_id from user_likes where user_id in ({placeholders})",
        tuple(cohort),
    ).fetchall()
    liked_unrated = 0
    for user_id, tmdb_id in like_rows:
        if tmdb_id is None:
            continue
        if (int(user_id), int(tmdb_id)) in rated_by_user:
            continue
        liked_unrated += 1
        positives[int(tmdb_id)].append(1)

    total = sum(len(v) for v in positives.values())
    positive = sum(sum(v) for v in positives.values())
    return positives, {
        "rated_interactions": rated,
        "liked_unrated_interactions": liked_unrated,
        "total_interactions": total,
        "positive_interactions": positive,
        "base_rate": round(positive / total, 6) if total else 0.0,
    }


def _load_theme_labels(con: sqlite3.Connection) -> dict[int, dict[str, str]]:
    """Film -> {normalized theme id: display label}, matching features.parse_themes."""
    film_labels: dict[int, dict[str, str]] = defaultdict(dict)
    for tmdb_id, raw in con.execute("select tmdb_id, themes from movie_cache"):
        if tmdb_id is None or not raw:
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if isinstance(item, dict):
                label = (item.get("label") or item.get("id") or "").strip()
                key = item.get("id") or (label.casefold() if label else "")
            elif isinstance(item, str):
                label = item.strip()
                key = label.casefold()
            else:
                continue
            if key and label:
                film_labels[int(tmdb_id)][key] = label
    return film_labels


def _load_subniche_labels(con: sqlite3.Connection) -> dict[int, dict[str, str]]:
    """Film -> {full cluster label: display label} for above-threshold memberships.

    Keyed by the full cluster label because that is the identity the ranking
    side builds subniche affinity with (services.features.load_subniche_map).
    """
    film_labels: dict[int, dict[str, str]] = defaultdict(dict)
    rows = con.execute(
        """
        select fs.tmdb_id, tc.slug, tc.label, fs.weight
        from film_subniches fs
        join theme_clusters tc on tc.id = fs.cluster_id
        where fs.weight >= ?
        """,
        (SUBNICHE_MIN_WEIGHT,),
    ).fetchall()
    for tmdb_id, _slug, label, _weight in rows:
        if tmdb_id is None or not label:
            continue
        film_labels[int(tmdb_id)][label] = label.split(" · ")[0]
    return film_labels


def _measure(labels_by_film, positives) -> tuple[list[dict], dict]:
    """Per-label support/positive/lift plus a summary block."""
    base_rate = sum(sum(v) for v in positives.values()) / max(
        1, sum(len(v) for v in positives.values())
    )
    stats: dict[str, dict] = {}
    for film_id, labels in labels_by_film.items():
        film_positive = positives.get(film_id)
        if not film_positive:
            continue
        for key, label in labels.items():
            entry = stats.setdefault(
                key, {"label": label, "support": 0, "positive": 0}
            )
            entry["support"] += len(film_positive)
            entry["positive"] += sum(film_positive)

    rows = []
    for key, entry in stats.items():
        pos_rate = entry["positive"] / entry["support"]
        rows.append(
            {
                "id": key,
                "label": entry["label"],
                "support": entry["support"],
                "positive": entry["positive"],
                "pos_rate": round(pos_rate, 6),
                "lift": round(pos_rate / base_rate, 4) if base_rate else None,
            }
        )
    rows.sort(key=lambda r: (-(r["lift"] or 0), -r["support"]))
    summary = {
        "labels_total": len(rows),
        "median_support": _median([r["support"] for r in rows]),
    }
    return rows, summary


def _median(values: list) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _filter_measured(rows: list[dict], min_support: int) -> list[dict]:
    measured = [row for row in rows if row["support"] >= min_support and row["lift"]]
    above = sum(1 for row in measured if row["lift"] > 1.0)
    summary_share = {
        "min_support": min_support,
        "labels_measured": len(measured),
        "share_above_base": round(above / len(measured), 4) if measured else 0.0,
        "median_lift": _median([row["lift"] for row in measured]),
    }
    return measured, summary_share


def _table(rows: list[dict], limit: int, *, worst: bool = False) -> str:
    selected = rows[-limit:] if worst else rows[:limit]
    lines = ["| label | support | positive rate | lift |", "|---|---:|---:|---:|"]
    for row in selected:
        lines.append(
            f"| {row['label'][:60]} | {row['support']} | "
            f"{row['pos_rate']:.1%} | {row['lift']:.2f} |"
        )
    return "\n".join(lines)


def _markdown(report: dict) -> str:
    cfg = report["config"]
    interactions = report["interactions"]
    lines = [
        f"# Label lift ({report['captured_at'][:10]})",
        "",
        f"- db: `{report['db']}`",
        f"- interactions: {interactions['total_interactions']:,} "
        f"({interactions['positive_interactions']:,} positive, "
        f"base rate {interactions['base_rate']:.1%})",
        f"- positive threshold: rating ≥ {cfg['min_rating']}, "
        f"min support {cfg['min_support']}",
        "",
        "## Subniches — strongest",
        "",
        _table(report["subniches"]["measured"], 12),
        "",
        "## Subniches — weakest",
        "",
        _table(report["subniches"]["measured"], 8, worst=True),
        "",
        "## Themes — strongest",
        "",
        _table(report["themes"]["measured"], 12),
        "",
        "## Themes — weakest",
        "",
        _table(report["themes"]["measured"], 8, worst=True),
        "",
        "Lift > 1.0 means films carrying the label are more likely to be",
        "positively received than the average rated film. The measured tables",
        "exclude labels below the support floor; the JSON keeps everything.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--out", type=Path, default=BACKEND / "reports" / "label_lift.json")
    parser.add_argument("--min-rating", type=float, default=4.0)
    parser.add_argument("--min-support", type=int, default=30)
    args = parser.parse_args()

    source = args.db.resolve()
    if not source.exists():
        print(f"db does not exist: {source}", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        positives, interactions = _load_interactions(con, args.min_rating)
        if interactions["total_interactions"] == 0:
            print("no cohort interactions found", file=sys.stderr)
            return 2

        theme_rows, theme_summary = _measure(_load_theme_labels(con), positives)
        subniche_rows, subniche_summary = _measure(_load_subniche_labels(con), positives)
        theme_measured, theme_share = _filter_measured(theme_rows, args.min_support)
        subniche_measured, subniche_share = _filter_measured(subniche_rows, args.min_support)

        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "db": str(source),
            "config": {"min_rating": args.min_rating, "min_support": args.min_support},
            "interactions": interactions,
            "themes": {
                "summary": {**theme_summary, **theme_share},
                "measured": theme_measured,
                "all": theme_rows,
            },
            "subniches": {
                "summary": {**subniche_summary, **subniche_share},
                "measured": subniche_measured,
                "all": subniche_rows,
            },
        }
    finally:
        con.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    args.out.with_suffix(".md").write_text(_markdown(report))

    print(
        f"interactions: {interactions['total_interactions']:,} "
        f"(base rate {interactions['base_rate']:.1%})"
    )
    print(
        f"subniches: {subniche_share['labels_measured']} measured, "
        f"{subniche_share['share_above_base']:.0%} above base, "
        f"median lift {subniche_share['median_lift']:.2f}"
    )
    print(
        f"themes: {theme_share['labels_measured']} measured, "
        f"{theme_share['share_above_base']:.0%} above base, "
        f"median lift {theme_share['median_lift']:.2f}"
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
