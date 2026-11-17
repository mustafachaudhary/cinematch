"""Quick backend smoke test: migration + all main endpoints."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from main import app

with TestClient(app) as client:
    print("ROOT:", client.get("/").json())

    r = client.get("/movies/engine-status")
    print("ENGINE-STATUS:", r.status_code)
    print(r.json())

    r = client.get("/movies/feed", headers={"X-Username": "mustafa"})
    data = r.json()
    print(f"FEED: {r.status_code} type={data.get('feed_type')} n={len(data.get('results', []))}")
    for item in data.get("results", [])[:3]:
        print("  -", item["title"], "| score:", item["score"], "| reason:", item["reason"])

    r = client.get("/movies/search", params={"q": "mind-bending thriller"}, headers={"X-Username": "mustafa"})
    data = r.json()
    print(f"SEARCH mood-ish: {r.status_code} mode={data.get('mode')} n={len(data.get('results', []))}")
    for item in data.get("results", [])[:3]:
        print("  -", item["title"], "| score:", item["score"])

    r = client.get("/movies/search", params={"q": "something like Interstellar"}, headers={"X-Username": "mustafa"})
    data = r.json()
    print(f"SEARCH anchor: {r.status_code} mode={data.get('mode')} ref={data.get('reference_title')}")
    for item in data.get("results", [])[:3]:
        print("  -", item["title"], "| score:", item["score"], "|", item["reason"])

    # Log two movies to activate personalization
    r = client.post("/logs/", json={"tmdb_id": 157336, "rating": 5.0}, headers={"X-Username": "mustafa"})
    print("LOG interstellar:", r.status_code, bool(r.json()))
    r = client.post("/logs/", json={"tmdb_id": 550, "rating": 4.5}, headers={"X-Username": "mustafa"})
    print("LOG fight club:", r.status_code)

    r = client.post(
        "/reviews/157336",
        json={"content": "Absolutely stunning experience, one of the greatest films ever made. Loved every minute!"},
        headers={"X-Username": "mustafa"},
    )
    print("REVIEW:", r.status_code, r.json().get("sentiment"))

    r = client.get("/movies/feed?limit=8", headers={"X-Username": "mustafa"})
    data = r.json()
    print(f"FEED personalized: {r.status_code} type={data.get('feed_type')}")
    print("  summary:", data.get("profile_summary"))
    for item in data.get("results", [])[:5]:
        sig = item.get("signals") or {}
        print("  -", item["title"], "| score:", item["score"], "| cf:", "cf" in sig, "|", item["reason"])

    r = client.get("/movies/profile-summary", headers={"X-Username": "mustafa"})
    print("PROFILE-SUMMARY:", r.status_code, r.json())

    r = client.get("/logs/", headers={"X-Username": "mustafa"})
    print("LOGS:", r.status_code, len(r.json()))

print("\nSMOKE OK")
