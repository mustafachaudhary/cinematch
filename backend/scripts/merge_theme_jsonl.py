"""Merge dataset themes and scraped page themes into one applyable JSONL."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TOP = BACKEND / "letterboxd_data" / "top_logged"


def read_rows(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=TOP / "letterboxd_themes.jsonl")
    parser.add_argument("--scraped", type=Path, default=TOP / "themes_scraped.jsonl")
    parser.add_argument("--out", type=Path, default=TOP / "themes_all.jsonl")
    args = parser.parse_args()

    combined = {}
    counts = Counter()
    for row in read_rows(args.dataset):
        tmdb_id = row.get("tmdb_id")
        if tmdb_id:
            combined[int(tmdb_id)] = row
            counts["dataset"] += 1
    for row in read_rows(args.scraped):
        tmdb_id = row.get("tmdb_id")
        if not tmdb_id:
            continue
        status = row.get("status")
        if status not in {"ok", "no_themes", "missing_slug", "http_404"}:
            counts[f"ignored_{status}"] += 1
            continue
        if status == "http_404":
            counts["scraped_404"] += 1
            continue
        combined[int(tmdb_id)] = {
            "tmdb_id": int(tmdb_id),
            "slug": row.get("slug"),
            "title": row.get("title"),
            "year": row.get("year"),
            "themes": row.get("themes") or [],
            "source": "letterboxd_page" if status == "ok" else "letterboxd_page_empty",
        }
        counts[status] += 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for tmdb_id in sorted(combined):
            handle.write(json.dumps(combined[tmdb_id], ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(combined), "counts": counts, "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
