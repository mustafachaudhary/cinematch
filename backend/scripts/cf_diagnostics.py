"""Health report for item-item collaborative filtering.

Prints per-rater prediction coverage and confidence distribution against the
feed's honesty gate (confidence >= 0.25) so CF tuning can be measured, not
guessed.

Run:  python scripts/cf_diagnostics.py [--user USERNAME]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.database import Film, SessionLocal, User
from services.collab import (
    CONFIDENCE_CAP,
    _center,
    item_similarity,
    load_rating_matrix,
    predict_for_user,
)

REASON_GATE = 0.25


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="restrict detail output to one username")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        matrix = load_rating_matrix(db)
        means, centered = _center(matrix)
        sims = item_similarity(centered)
        pairs = sum(len(row) for row in sims.values()) // 2

        print(
            f"raters={len(matrix)}  "
            f"ratings={sum(len(items) for items in matrix.values())}  "
            f"similarity_pairs={pairs}"
        )
        print(f"honesty gate for feed reasons: confidence >= {REASON_GATE}\n")

        titles = dict(db.query(Film.film_id, Film.title).all())
        usernames = dict(db.query(User.user_id, User.username).all())

        all_max = []
        gated_total = 0
        for user_id, ratings in sorted(matrix.items()):
            preds = predict_for_user(ratings, sims)
            name = usernames.get(user_id, f"user{user_id}")
            if not preds:
                print(f"{name}: no CF predictions")
                continue
            confs = [p["confidence"] for p in preds.values()]
            gated = sum(1 for c in confs if c >= REASON_GATE)
            gated_total += gated
            all_max.append(max(confs))
            print(
                f"{name}: rated {len(ratings)} films, "
                f"{len(preds)} predictable, "
                f"max_conf={max(confs):.3f}, mean_conf={sum(confs) / len(confs):.3f}, "
                f"past_gate={gated}"
            )
            if args.user and args.user.lower() in name.lower():
                top = sorted(preds.items(), key=lambda kv: kv[1]["confidence"], reverse=True)[:10]
                for film_id, p in top:
                    print(
                        f"    {titles.get(film_id, film_id)}: "
                        f"pred {p['pred']:.2f}* conf {p['confidence']:.3f}"
                    )

        if all_max:
            print(
                f"\noverall: best_conf={max(all_max):.3f} of cap {CONFIDENCE_CAP}, "
                f"predictions past gate across all raters: {gated_total}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
