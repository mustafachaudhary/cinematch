"""Latency benchmark for the CineMatch API (Phase 0 baseline).

Usage:
    cd backend && ../.venv/bin/python scripts/bench.py --user mustafa \
        --out reports/bench_baseline.json

Measures p50/p95/max latency for the endpoints that will be re-budgeted in
Phase 4 (feed, search, engine-status). Run it again after the scale work and
fail the gate when budgets are exceeded.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ENDPOINTS = {
    "feed": "/movies/feed?limit=20",
    "search": "/movies/search?q=interstellar&limit=16",
    "engine_status": "/movies/engine-status",
}


def timed_get(base: str, path: str, headers: dict, timeout: float) -> float:
    start = time.perf_counter()
    response = requests.get(f"{base}{path}", headers=headers, timeout=timeout)
    elapsed = time.perf_counter() - start
    response.raise_for_status()
    return elapsed


def bench(base: str, user: str, n: int, warmup: int, timeout: float) -> dict:
    headers = {"X-Username": user}
    results: dict[str, dict] = {}
    for name, path in ENDPOINTS.items():
        for _ in range(warmup):
            timed_get(base, path, headers, timeout)
        samples = [timed_get(base, path, headers, timeout) for _ in range(n)]
        samples_ms = sorted(value * 1000 for value in samples)
        results[name] = {
            "path": path,
            "n": n,
            "mean_ms": round(statistics.mean(samples_ms), 2),
            "p50_ms": round(statistics.median(samples_ms), 2),
            "p95_ms": round(samples_ms[max(0, int(len(samples_ms) * 0.95) - 1)], 2),
            "max_ms": round(samples_ms[-1], 2),
        }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "user": user,
        "endpoints": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="mustafa")
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = bench(args.base, args.user, args.n, args.warmup, args.timeout)
    for name, values in report["endpoints"].items():
        print(
            f"{name:14s} p50 {values['p50_ms']:7.2f} ms  p95 {values['p95_ms']:7.2f} ms  "
            f"max {values['max_ms']:7.2f} ms"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2))
        print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
