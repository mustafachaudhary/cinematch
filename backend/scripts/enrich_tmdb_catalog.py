"""Enrich the local catalog with TMDB metadata, credits, keywords and edges.

One TMDB request per film (``append_to_response=credits,keywords,external_ids,
similar,recommendations``), checkpointed to JSONL and written to SQLite in
small batches. The script is restart-safe: committed batches are recorded in
the checkpoint and replayed into a fresh target DB without re-fetching.

Usage:
    cd backend && ../.venv/bin/python scripts/enrich_tmdb_catalog.py \
        --db /tmp/opencode/.../copy.db
    ... --limit 50 --workers 4       # smoke test
    ... --force                      # refetch every catalog row
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LIVE_DB = (BACKEND / "cinematch.db").resolve()
sys.path.insert(0, str(BACKEND))


def _load_checkpoint(path: Path) -> dict[int, dict]:
    records: dict[int, dict] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        tmdb_id = row.get("tmdb_id")
        if tmdb_id:
            records[int(tmdb_id)] = row
    return records


def _apply(con, payload: dict) -> None:
    from sqlalchemy import delete, update

    from models.database import FilmPerson, FilmRelated, MovieCache

    tmdb_id = int(payload["tmdb_id"])
    values = {
        "runtime": payload.get("runtime"),
        "original_language": payload.get("original_language"),
        "vote_count": payload.get("vote_count"),
        "collection_id": payload.get("collection_id"),
        "imdb_id": payload.get("imdb_id"),
        "keywords": json.dumps(payload.get("keywords") or [], ensure_ascii=False),
    }
    result = con.execute(
        update(MovieCache).where(MovieCache.tmdb_id == tmdb_id).values(**values)
    )
    if not result.rowcount:
        return

    con.execute(delete(FilmPerson).where(FilmPerson.tmdb_id == tmdb_id))
    people = payload.get("people") or []
    if people:
        con.execute(
            FilmPerson.__table__.insert(),
            [
                {
                    "tmdb_id": tmdb_id,
                    "person_id": person["person_id"],
                    "name": person["name"],
                    "role": person["role"],
                    "ordering": person.get("ordering"),
                    "profile_path": person.get("profile_path"),
                    "source": "tmdb",
                }
                for person in people
            ],
        )

    con.execute(
        delete(FilmRelated).where(
            FilmRelated.tmdb_id == tmdb_id,
            FilmRelated.source.in_(("similar", "recommendations")),
        )
    )
    related = payload.get("related") or []
    if related:
        con.execute(
            FilmRelated.__table__.insert(),
            [
                {
                    "tmdb_id": tmdb_id,
                    "related_tmdb_id": row["related_tmdb_id"],
                    "source": row["source"],
                    "rank": row.get("rank"),
                }
                for row in related
            ],
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=BACKEND / "letterboxd_data" / "seeds" / "tmdb_enrichment.jsonl",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rps", type=float, default=8.0)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live:
        print("refusing live DB without --allow-live", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    from sqlalchemy import func

    from models.database import MovieCache, SessionLocal
    from services import tmdb

    started = time.monotonic()
    checkpoint = _load_checkpoint(args.checkpoint)
    db = SessionLocal()
    checkpoint_fh = None
    try:
        total = db.query(func.count(MovieCache.tmdb_id)).scalar() or 0
        if args.force:
            targets = [
                row
                for row in db.query(
                    MovieCache.tmdb_id, MovieCache.title, MovieCache.release_year
                ).all()
            ]
        else:
            targets = [
                row
                for row in db.query(
                    MovieCache.tmdb_id, MovieCache.title, MovieCache.release_year
                )
                .filter(
                    (MovieCache.runtime.is_(None))
                    | (MovieCache.keywords.is_(None))
                )
                .all()
            ]
        targets.sort(key=lambda row: int(row[0]))
        if args.limit:
            targets = targets[: args.limit]

        print(
            json.dumps(
                {
                    "db": str(db_path),
                    "catalog": total,
                    "targets": len(targets),
                    "checkpoint_records": len(checkpoint),
                },
                indent=2,
            )
        )
        if not targets:
            return 0

        # Replay already-fetched records first; this makes a fresh copy cheap
        # and keeps the network pass idempotent.
        replayed = 0
        pending = []
        for tmdb_id, title, year in targets:
            payload = checkpoint.get(int(tmdb_id))
            if payload and not args.force:
                _apply(db, payload)
                replayed += 1
            else:
                pending.append({"tmdb_id": int(tmdb_id), "title": title, "year": year})
        if replayed:
            db.commit()
            print(f"replayed {replayed} checkpoint records")

        args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_fh = args.checkpoint.open("a", encoding="utf-8")
        tmdb.configure_rate_limit(args.rps)

        buffered: list[dict] = []
        errors = []
        fetched = 0
        applied = 0

        def flush():
            nonlocal buffered, applied
            if not buffered:
                return
            for payload in buffered:
                _apply(db, payload)
            db.commit()
            for payload in buffered:
                checkpoint_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            checkpoint_fh.flush()
            applied += len(buffered)
            buffered = []

        if pending:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                futures = {
                    pool.submit(tmdb.get_movie_metadata, row["tmdb_id"]): row
                    for row in pending
                }
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        payload = future.result()
                    except Exception as exc:  # noqa: BLE001
                        errors.append({"tmdb_id": row["tmdb_id"], "error": repr(exc)})
                        continue
                    fetched += 1
                    if payload:
                        buffered.append(payload)
                    if len(buffered) >= args.batch_size:
                        flush()
                    if fetched % 100 == 0 or fetched == len(pending):
                        elapsed = max(0.001, time.monotonic() - started)
                        print(
                            f"  fetched {fetched}/{len(pending)} "
                            f"applied={applied} errors={len(errors)} "
                            f"rate={fetched / elapsed:.1f}/s"
                        )
                flush()

        stats = {
            "catalog": total,
            "targets": len(targets),
            "replayed": replayed,
            "fetched": fetched,
            "applied": applied,
            "errors": errors[:20],
            "error_count": len(errors),
            "with_keywords": db.query(func.count(MovieCache.tmdb_id))
            .filter(MovieCache.keywords.isnot(None))
            .scalar()
            or 0,
            "with_runtime": db.query(func.count(MovieCache.tmdb_id))
            .filter(MovieCache.runtime.isnot(None))
            .scalar()
            or 0,
            "people_rows": 0,
            "related_rows": 0,
        }
        from models.database import FilmPerson, FilmRelated

        stats["people_rows"] = db.query(func.count(FilmPerson.id)).scalar() or 0
        stats["related_rows"] = db.query(func.count(FilmRelated.id)).scalar() or 0
        stats["elapsed_seconds"] = round(time.monotonic() - started, 1)

        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "db": str(db_path),
            "checkpoint": str(args.checkpoint),
            "stats": stats,
        }
        out_path = args.out or (
            BACKEND / "reports" / f"tmdb_enrichment_{args.tag}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))
        print(json.dumps(stats, indent=2))
        print(f"report: {out_path}")
        return 0 if not errors else 1
    finally:
        if checkpoint_fh:
            checkpoint_fh.close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
