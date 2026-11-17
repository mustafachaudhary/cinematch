"""Batch-embed every catalog film that is missing a current embedding.

Used after a seed import (or catalog enrichment) so the feed/evaluation never
falls back to the slow pairwise MMR path for thousands of movies. The script
sets ``DATABASE_URL`` before importing the app models, so it can target a copy
without touching the live DB.

Usage:
    cd backend && ../.venv/bin/python scripts/embed_catalog.py \
        --db /tmp/opencode/cinematch_seed_import/cinematch_seed_import.db
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    from sqlalchemy import func

    from models.database import MovieCache, SessionLocal
    from services import embeddings

    db = SessionLocal()
    try:
        total = db.query(func.count(MovieCache.tmdb_id)).scalar() or 0
        missing = (
            db.query(func.count(MovieCache.tmdb_id))
            .filter(MovieCache.embedding.is_(None))
            .scalar()
            or 0
        )
        signature = embeddings.active_signature(db)
        print(
            json.dumps(
                {
                    "db": str(db_path),
                    "catalog": total,
                    "missing_before": missing,
                    "signature": signature,
                },
                indent=2,
            )
        )
        if args.dry_run:
            return 0
        started = time.monotonic()
        stats = embeddings.embed_movies(
            db, batch_size=args.batch_size, force=args.force
        )
        stats["elapsed_seconds"] = round(time.monotonic() - started, 1)
        stats["catalog"] = total
        stats["missing_before"] = missing
        print(json.dumps(stats, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
