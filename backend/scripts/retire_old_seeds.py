"""Retire legacy ``lb_*`` seed accounts that have no new-cohort provenance.

The new cohort is identified by a ``seed_profile`` row. Anything flagged
``is_seed=1`` without that row is a pre-v2 seed and is removed in one
transaction, in dependency order. mustafa is excluded by a hard guard and its
password/counts are verified before and after.

Usage:
    cd backend && ../.venv/bin/python scripts/retire_old_seeds.py \
        --db /tmp/opencode/.../copy.db --dry-run
    ... --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from import_seed_profiles import mustafa_snapshot  # noqa: E402

LIVE_DB = (BACKEND / "cinematch.db").resolve()


def table_names(con: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in con.execute("select name from sqlite_master where type='table'")
    ]


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"pragma table_info('{table}')")}


def legacy_seeds(con: sqlite3.Connection) -> list[tuple[int, str]]:
    return [
        (int(user_id), username)
        for user_id, username in con.execute(
            """
            select u.user_id, u.username
            from users u
            left join seed_profile s on s.user_id = u.user_id
            where u.is_seed = 1 and s.id is null
            order by u.username
            """
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db.resolve()
    if db_path == LIVE_DB and not args.allow_live:
        print("refusing live DB without --allow-live", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    con = sqlite3.connect(db_path, timeout=30)
    con.execute("pragma foreign_keys=on")
    con.execute("pragma busy_timeout=30000")
    con.execute("pragma journal_mode=wal")

    seeds = legacy_seeds(con)
    if not seeds:
        print("no legacy seeds found; nothing to retire")
        con.close()
        return 0
    if any(username == "mustafa" for _, username in seeds):
        print("refusing to retire mustafa", file=sys.stderr)
        con.close()
        return 3

    before = mustafa_snapshot(con)
    seed_ids = {user_id for user_id, _ in seeds}
    tables = table_names(con)
    counts: dict[str, int] = {}
    for table in tables:
        if table == "users":
            continue
        cols = columns(con, table)
        if "user_id" in cols:
            placeholders = ",".join("?" for _ in seed_ids)
            n = con.execute(
                f"delete from {table} where user_id in ({placeholders})",
                tuple(seed_ids),
            ).rowcount
            counts[table] = counts.get(table, 0) + n

    placeholders = ",".join("?" for _ in seed_ids)
    counts["user_profile"] = counts.get("user_profile", 0) + con.execute(
        f"delete from user_profile where username in ({placeholders})",
        tuple(username for _, username in seeds),
    ).rowcount
    counts["users"] = con.execute(
        f"delete from users where user_id in ({placeholders})", tuple(seed_ids)
    ).rowcount
    counts["orphan_films"] = con.execute(
        "delete from films where film_id not in (select distinct film_id from ratings)"
    ).rowcount

    after = mustafa_snapshot(con)
    if before != after:
        con.rollback()
        print(
            "MUSTAFA INVARIANT VIOLATION; rolled back\n"
            f"before={json.dumps(before, sort_keys=True)}\n"
            f"after={json.dumps(after, sort_keys=True)}",
            file=sys.stderr,
        )
        con.close()
        return 3

    remaining = con.execute("select count(*) from users where is_seed = 1").fetchone()[0]
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "applied": bool(args.apply),
        "retired": [{"username": username} for _, username in seeds],
        "deleted_rows": counts,
        "remaining_seeds": remaining,
        "mustafa_before": before,
        "mustafa_after": after,
    }

    if args.apply:
        con.commit()
        print(json.dumps(report, indent=2))
    else:
        con.rollback()
        print(json.dumps(report, indent=2))
        print("dry run: rolled back")

    con.close()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
