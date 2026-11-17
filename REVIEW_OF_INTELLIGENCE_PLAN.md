# Adversarial Review of INTELLIGENCE_PLAN v2

Repo: `/home/mustafa/Downloads/Odin/NLP/cinematch.ai`
Live DB inspected read-only: `backend/cinematch.db` (SQLite, mode=ro). Python: `.venv/bin/python` (3.14.8). No live network calls to Letterboxd/TMDB were made; all claims are backed by static code reads and DB queries.

## Verified baseline facts (plan §0 is accurate)

| Claim | Evidence | Status |
|---|---|---|
| 20 `lb_*` seeds, 2,291 ratings | `SELECT COUNT(*) FROM ratings WHERE username LIKE 'lb_%'` → 2291; 20 seed users | CONFIRMED |
| 380/1,904 films ≥2 ratings; 770 zero | `films>=2 ratings → 380`; `tmdb_id NOT IN ratings → 770` | CONFIRMED |
| mustafa overlap 89/623 | join query → 89; mustafa ratings → 623 | CONFIRMED |
| "286/1,280 CF-supported candidates" | measured 1281 non-rated candidates, 287 with CF predictions for mustafa | CONFIRMED (off by 1) |
| Corruption: 5★→0.5★ via `rated-(\d)` | `scripts/import_letterboxd_profiles.py:25,56` — `RATED_CLASS = re.compile(r"rated-(\d")`, `int(group(1))/2` so `rated-10` → 0.5. DB: **zero** seed users have any 5.0 rating; 0.5 buckets are huge (e.g. lb_agastak 43×0.5 of 112) | CONFIRMED |
| Themes: 598 entries, 591 themed, 108 labels, title-keyed, not used in scoring | `browser_scraped_themes.json`; 0 theme labels overlap lowercased genres; themes never referenced in `services/` or `routers/` | CONFIRMED |
| Ranking 0.85·content + 0.15·vote_average; positive-only sims; rebuild-on-change | `ranking.py:20-25`, `collab.py:76` `if sim > 0`, `collab.py:172-182` fingerprint rebuild | CONFIRMED |
| Search skips the anchor film itself | `search.py:178-179` `if movie.tmdb_id == anchor.tmdb_id: continue` | CONFIRMED |
| Watched-unrated excluded from feed; cinephile 588 watched w/ tmdb_id all invisible | `routers/movies.py:46-55`; DB: cinephile watched=588 w/ tmdb, ratings=0 | CONFIRMED |
| ZIP import blocks event loop | `routers/profile.py:376` `async def` with **no awaits**; sync zipfile+csv+per-row TMDB at :417-445; no size cap | CONFIRMED |
| No `imdb_id`/`vote_count` columns; tmdb.py never stores them | DB `PRAGMA table_info(movie_cache)` → no such columns; `tmdb.py:317-326` `_normalize` omits vote_count; `ensure_cached_movie` (`ratings.py:14-39`) doesn't write imdb_id even though `get_movie_full` fetches it (:262) | CONFIRMED |
| No feedback/impressions/hidden/watched-unrated/onboarding endpoints anywhere | grep across routers/, services/, frontend/api.js — zero matches | CONFIRMED |
| scipy 1.18.1 and sentence-transformers importable in venv | direct import check | CONFIRMED |
| uvicorn binds 127.0.0.1 by default (localhost-only holds) | `uvicorn.config.Config.__init__` host default = '127.0.0.1'; `run_server.py:11` explicit | CONFIRMED |
| Test suite: 43 tests | `grep -c "def test" test_engine.py → 43` | CONFIRMED |

---

## P0 — blockers / security / data-integrity (must change before any rollout)

### P0-1. Full account impersonation via `X-Username`; seed users have no passwords
**Evidence:** `routers/deps.py:16-32` — `resolve_user` returns any existing `User` from the header alone, no password/session check. `routers/profile.py:209-221` `get_profile` and :295 `update_profile` trust the header identically. `scripts/import_letterboxd_profiles.py:87-90` creates `lb_*` users with no `password_hash`. So `X-Username: lb_davidehrlich` gives full access to that account (feed, logs, ratings, profile edit) with no credentials. The plan's mitigation (`LOCAL_TRUST_HEADER` flag + optional token path) **does not exist in code** — the flag is never read anywhere.
**Why it matters:** This is the plan's #1 security hole, and the plan's own mitigation is vapor. Localhost-only binding reduces but does not remove it (any local process / same LAN if bound to 0.0.0.0 via `start.sh`'s unqualified uvicorn — uvicorn defaults to 127.0.0.1, but `start.sh` runs `python -m uvicorn main:app --reload` with no host, relying on that default).
**Smallest plan change:** Add `LOCAL_TRUST_HEADER` env check in `resolve_user`: refuse `X-Username` impersonation unless the flag is explicitly true AND the request is local; ship the additive HMAC/session-token login path (login → token → `Authorization: Bearer`) before any endpoint accepts cross-account traffic; mark seed users `is_seed=1 / password_hash=NULL` and explicitly disallow them from authenticating.

### P0-2. ZIP import is a synchronous, unbounded DoS in an `async` route
**Evidence:** `routers/profile.py:376-466` — `async def import_letterboxd_zip` performs `zipfile.ZipFile`, `zf.read`, per-row `tmdb.search_movies` (network!), `ensure_cached_movie` + `set_user_rating` with `db.commit()` per row, all blocking the FastAPI event loop. No size limit (plan proposes 50 MB — absent), no zip-bomb protection, no progress, no backgrounding. `api.importLetterboxd` (`frontend/src/hooks/api.js:79`) fires it with no timeout UI.
**Why it matters:** One large/malicious zip wedges the whole server until it finishes or times out. This is listed as P0 in the plan (§9-4) but zero code exists.
**Smallest plan change:** Implement the import with `starlette.concurrency.run_in_threadpool` (or a background task + job endpoint), enforce a 50 MB cap and a `zipfile` bomb check (total uncompressed size / entry count), and return a job ID with a polling endpoint. Do not merge this into the plan as "P0, done" — it's unwritten.

### P0-3. Exact-title search result is suppressed; a user cannot search→log the film they named
**Evidence:** `search.py:168-198` anchor mode `continue`s the anchor film (`search.py:178-179`); `routers/movies.py:87-113` returns anchor neighbors, no `reference` object, no `match_type: "title"`; frontend `SearchPage.jsx` renders results as films to pick, with no way to log the searched title from the results list.
**Why it matters:** The plan calls this "watched-unrated invisibility" / exact-title exclusion and marks the search→log fix P0 (§9-1, §14-2). As written, searching "Interstellar" never shows Interstellar.
**Smallest plan change:** In `search_movies`, when an anchor is found, prepend an exact `match_type: "title"` result carrying `{tmdb_id, year, poster}` (per §9-1), keep anchor neighbors below it, and add a frontend "Log this film" action on search results (POST /logs/). This is genuinely new code, not a config tweak.

### P0-4. SQLite + background model rebuilds: no worker session/locking story
**Evidence:** `models/database.py:33-36` engine uses `check_same_thread=False`, but SQLAlchemy `Session` objects are **not thread-safe**. `collab.py:141-197` process-wide caches are lock-protected but assume a single writer per process. The plan (§4-3) proposes a worker thread building `.npz` artifacts while requests serve — SQLite single-writer; concurrent connection writes risk `database is locked`. No worker `SessionLocal` pattern, no lock between rebuild thread and request path, no WAL/busy_timeout configuration.
**Why it matters:** "No request blocks on a rebuild" is the plan's core latency guarantee; without a concrete threading model it will fail with locked-database errors under load.
**Smallest plan change:** Specify in §4-3: the build worker opens its own short-lived `SessionLocal`, writes only artifacts (never the live DB during serving), and the main process swaps artifacts by atomic file rename; add `connect_args={"check_same_thread": False, "busy_timeout": 30000}` and enable WAL at engine creation. Add one concurrency test (rebuild thread + parallel feed requests).

### P0-5. TMDB request rate (8–10 req/s) violates TMDB's ~4/s limit
**Evidence:** plan §6.2: "~8–10 req/s, 422 backoff" — actually plan says "422 backoff"?? It says "429 backoff" (§6.2). TMDB's published limit is 40 requests / 10 seconds ≈ 4/s sustained. 8–10/s will earn HTTP 422 (rate limit exceeded), which `tmdb.py:_get` treats as a generic failure (`response.raise_for_status()` → exception) with no retry. Plan only backoffs on 429; 422 is not handled.
**Why it matters:** Phases 2-3 enrichment (thousands of TMDB calls) will systematically fail half an hour in; the plan's time estimates are wrong.
**Smallest plan change:** Cap TMDB calls at 4/s with a token bucket in `services/tmdb.py:_get`, retry on both 422 and 429 with jittered backoff, and add a `Retry-After` respect. Recompute Phase 2/3 request budgets at 4/s (see P1-3).

---

## P1 — architecture, data, evaluation (fix before Phase 4/5 gates)

### P1-1. Migration code for Phase 3 schema does not exist
**Evidence:** `models/database.py:351-355` `_add_missing_columns` adds only `display_name` and `password_hash`. Phase 3 (§6.3) requires new columns (`themes, keywords, runtime, original_language, vote_count, collection_id, imdb_id, letterboxd_slug, text_version` on `movie_cache`) and new tables (`film_people, film_related, user_feedback, feed_impressions, seed_profile`) — none are in `init_db` or any migration. `Base.metadata.create_all` never alters existing tables.
**Why it matters:** `vote_count` is required for the quality prior and the `vote_count ≥ 50` eligibility filter (§4-5); `imdb_id`/`collection_id` are required for resolution and franchise affinity (§8.1). Phase 3 cannot run as written.
**Smallest plan change:** Add explicit `ALTER TABLE ... ADD COLUMN` statements (guarded by `PRAGMA table_info`) plus `CREATE TABLE IF NOT EXISTS` for the five new tables to `_add_missing_columns`, ordered so re-runs are idempotent; add a migration test that applies them to a copy of the live DB.

### P1-2. Existing 20 seed profiles stay corrupted; gate doesn't require re-importing them
**Evidence:** DB confirms zero seed 5.0s and bloated 0.5 buckets (mechanism at `import_letterboxd_profiles.py:25,56`). Plan §5.2 replaces seed ratings on import, but G1 (§5.3) demands "zero corruption markers" only for the ≥60 **new** profiles — the 20 existing `lb_*` profiles (2,291 ratings, the whole current CF basis) would remain corrupt unless re-harvested. `collab.py` top predictions for mustafa are all 5.0★ with confidence ~0.02–0.06 — a direct artifact of neighbors' corrupted 0.5★s.
**Why it matters:** CF similarity and the cold-start matrix are built on systematically inverted data; any ablation lift measured on top of it is noise.
**Smallest plan change:** Re-scrape/re-import the existing 20 seeds as part of Phase 1 (same pipeline as new profiles), and make G1's "zero corruption" check apply to **all** seed users, not just new ones.

### P1-3. Phase 2/3 request budget is under-specified and likely wrong
**Evidence:** ~30k seed ratings resolve to roughly 4–5k unique films. Plan §5.1: 1 Letterboxd film-page fetch + 1 TMDB `/find/<imdb_id>` per unique film ≈ 8–10k requests; §6.1: another 1 LB page per film without a slug for themes; §6.2: 1 `movie/{id}?append_to_response=…` per film ≈ 5k calls. At the required 4/s cap, that's ≈ 45–75 minutes of wall-clock for resolution+enrichment before themes; plan gives no hour estimate and no resume strategy for TMDB failures beyond "checkpointed" (§6.2).
**Why it matters:** "Idempotent, resumable" is asserted; the scripts (`resolve_seed_ratings.py`, `enrich_tmdb_catalog.py`, `scrape_letterboxd_films.py`) don't exist yet, and the budget determines whether the phase is a day or a week.
**Smallest plan change:** Add request-count math to §5/§6 (unique films × endpoints × 4/s), require a persistent `seeds/tmdb_cache.json` with failure retry queue, and cap per-run wall time with resume (the plan says resumable — make the checkpoint granularity explicit: per-film, not per-batch).

### P1-4. Themes are title-keyed, cover only 587/598 of current cache titles, and lack slugs
**Evidence:** `browser_scraped_themes.json`: 598 entries keyed by plain title; 587/598 match `movie_cache` titles; "The Odyssey" exists 3× in the cache (two 2026, one 2016) — title-only lookup is ambiguous; labels (not Letterboxd slugs) are stored, but plan §6.1 canonicalizes by "theme slug → canonical id"; no slug column exists anywhere. Films without themes (≈1,300 of 1,904) simply get no `theme_match` — gated by "≥5 themed ratings" (§8.1), so cold users never see it.
**Why it matters:** The plan's theme pipeline (§6.1) assumes slugs exist in the data; they don't. Applying themes by title will mislabel remakes/duplicates.
**Smallest plan change:** Store `letterboxd_slug` on `movie_cache` (migration P1-1), re-scrape theme pages to capture slugs (or derive slugs from labels as a transitional key), key the theme table by `tmdb_id` not title, and make `theme_match` degrade gracefully (skip term) when a film has no themes instead of requiring ≥5 themed ratings per user.

### P1-5. MiniLM reordering helps but the 256-wordpiece budget is untested and borderline
**Evidence:** Current `movie_text` (`embeddings.py:33-36`) = `"{title}. {overview} Genres: ..."` — overview first, so long overviews push genres out of MiniLM's 256 wordpieces. Plan §6.4 reorders to `Title (year). Themes: …. Genres: …. Overview (first ~120 words).` No measurement of overview length distribution in the catalog; no truncation test; "first ~120 words" ≈ 160–190 wordpieces, leaving only ~60 for title+themes+genres — long theme lists (Interstellar has 8) can still overflow.
**Why it matters:** The plan claims themes "appended before a truncated overview will survive" — unproven; if genres/themes get truncated, embeddings ignore the very signal the plan adds.
**Smallest plan change:** Add a unit test embedding the longest catalog overview + themes and asserting genre/theme tokens are retained (or explicitly truncated with ellipsis + wordpiece count ≤ 250); define `text_version` bump and full re-embed rule (already in §6.4 — keep).

### P1-6. Ranking v2 is a 17-signal dynamic blend with an unproven evaluation design
**Evidence:** §8.1 lists 17 terms with per-candidate confidence weighting; §8.2 per-user isotonic/logistic calibration; gate G5: each retained signal ≥ +2% NDCG@20. Statistical traps unaddressed:
- **Personas are power users:** all 20 seeds are prolific raters (100–120 ratings each); warm holdout on them measures critic taste, not a new user.
- **Cold ladder is edge-level holdout:** hiding ratings of one user leaves that user's film ratings in the item-similarity matrix (other users rated those films), so `cf_item` leaks the held-out item; plan §3-3 says user-level holdout first but §9-1 cold ladder is edge-level by design — mixed messages.
- **Calibration sample size:** per-user calibration needs ≥ ~30+ rated films with binary like outcomes; seeds (~115) barely qualify, new users get the global fallback — so `match%` is uncalibrated for exactly the users the feed targets.
- **LOO + franchise duplicates:** "The Odyssey" ×3 and franchise clusters violate independence; dedupe of near-duplicates is mentioned (§3-3) but not specified.
- **match% interpretation:** switching from raw score to calibrated probability is an untested UX change; no baseline for what users expect.
**Why it matters:** The plan's central validity claim ("ranking v2 wins ablations") can be gamed by the leakage paths above, and G5's +2% NDCG has no baseline (set at G0, which hasn't run).
**Smallest plan change:** (i) Make the primary split **user-level** (entire personas out of the matrix, taste, theme, and person affinity); label the cold ladder explicitly as "edge-level, leakage-possible, supplementary." (ii) Restrict per-user calibration to users with ≥30 ratings; new users show score/percentile, not "calibrated probability," until MAE threshold is met. (iii) Dedup franchise/remake clusters in eval candidates. (iv) Set the NDCG baseline at G0 before accepting +2%.

### P1-7. Feedback loop and impressions are entirely unimplemented, frontend included
**Evidence:** `user_feedback` / `feed_impressions` tables not in DB or `models/database.py`; no `/movies/{id}/feedback`, `/movies/hidden`, `/movies/impressions` endpoints; `api.js` has no impression/feedback calls; `HomeFeed.jsx`/`PosterRun.jsx` only render links — no heart/✕, no impression posting, no hidden state. §8.4 fatigue (`0.9^impressions`, floor 0.6, "reset on interaction") has no defined application point (ranking? retrieval?) or client.
**Why it matters:** The plan presents the feedback loop as ready to wire (§8-4, §10-1); in reality the whole client→server→ranking path must be built. Double counting is possible if impressions are counted at render AND at click; fatigue can't work without render-confirmed events, which the frontend never sends.
**Smallest plan change:** Add the two tables (migration P1-1), the three endpoints, and frontend posting of impressions on scroll-into-view with (user, film, 12h) dedupe server-side; define fatigue as a multiplicative rank penalty applied at retrieval, reset on any explicit feedback. Without the frontend half, the loop is dead code — say so in §10.

### P1-8. Watch-list / rated-watched gap: no `watched-unrated` or onboarding endpoint
**Evidence:** `cinephile`: 588 watched films, 0 ratings, all invisible in feed (`routers/movies.py:46-55`). No `GET /movies/watched-unrated`, no `GET /movies/onboarding` (§10-2) — not in routers at all.
**Why it matters:** This is the plan's own "watched-unrated invisibility" fix (§0); without the endpoint the feed for new/imported users stays cold-start-only forever.
**Smallest plan change:** Implement `GET /movies/watched-unrated?limit&offset` (exclude rated, include watched-not-rated) and `GET /movies/onboarding` (24 diverse catalog picks), plus the LogForm already submits ratings — wire `onSaved` to refresh the feed (HomeFeed reload button exists; add automatic refetch on save).

### P1-9. Cache invalidation gaps in process-wide caches
**Evidence:** `collab.py:185-197` `_pred_cache` holds exactly ONE `(user_id, version)` entry — under concurrent users it thrashes (correctness OK, perf bad). `matrix_version` (`collab.py:147-163`) uses rounded rating sum — a theoretically possible (count, latest, sum) collision would serve stale sims; `rated_at` has `onupdate` so updates are caught. `embeddings.py:202-203` `_decode` lru_cache returns **the same shared array object** to all callers — documented read-only, but `rank_feed` `_mmr` divides `matrix / norms` (creates new, safe); a future caller mutating a decoded vector would corrupt all users.
**Why it matters:** Multi-user production use will thrash the prediction cache; a silent stale-sim collision is possible though unlikely.
**Smallest plan change:** Replace `_pred_cache` single entry with an LRU dict capped at 8 (matches §4-4 "per-user prediction LRU cap 8 users"); add a comment asserting decoded arrays are read-only. Keep `matrix_version` but add `MAX(rated_at)` instead of sum for collision resistance.

### P1-10. Scale numbers: decode cost alone threatens budgets at 10k
**Evidence:** Measured `decode_embedding` over 1,904 catalog rows = 0.07s (`search` + `feed` each decode every candidate per request). At 10k movies → ≈0.35–0.4s of pure JSON decode per feed/search, before any scoring — over the 150 ms search budget and a large share of the 250 ms feed budget. `routers/movies.py:35` `get_feed` loads `db.query(MovieCache).all()` (full rows, incl. 3.5 KB embedding JSON per row); `engine-status:121-126` decodes all embeddings per call.
**Why it matters:** The plan's budgets (feed ≤250 ms, search ≤150 ms at 10k) are only achievable with `EmbeddingStore` (load one float32 matrix at startup) + `load_only` queries + vectorized scoring — none of which exist yet. scipy is available (1.18.1), so the sparse-CF part is feasible; the store is the missing piece.
**Smallest plan change:** Implement `EmbeddingStore` (§4-1/§6.4) as Phase 4 code before any latency benchmark: load `(tmdb_id, vector)` once into a float32 matrix, serve feed/search from it, keep per-row JSON decode as fallback. Add `bench.py` with simulated 3k/6k/10k catalogs and hard fail above budget (G4).

### P1-11. Evaluation does not measure what a real new user experiences
**Evidence:** §6 warm personas = mustafa + seeds (all power users); §3-4 cold ladder hides seed users' own ratings but those users are still power-user taste profiles; no persona with 0 ratings going through the onboarding flow (§10-2 endpoint doesn't exist). "Evaluation-only watch lists" (§3-4) require scraping watched grids for 30 personas — more scraping with no cost estimate.
**Why it matters:** A plan whose gates pass on seed personas can still ship a feed that is useless for a first-time user (no ratings → cold_start_feed = quality×popularity, no personalization at all).
**Smallest plan change:** Add a "brand-new user" arm to Phase 6: 0 ratings, feed = cold_start + onboarding picks, measured by NDCG against that persona's eventual full history (simulated). Report it separately from warm-seed results.

### P1-12. Storage/disk and timeline are unspecified
**Evidence:** Plan §15/§12 have no disk estimate, no timeline, no backup schedule beyond "stop-server backup." 10k movies × ~3.5 KB embedding JSON ≈ 35 MB text + model artifacts (.npz ~ tens of MB) — trivial on disk, but not stated. Letterboxd full-history scrape: 49+ pages/user × 80 profiles × 1.2–2.2 s ≈ 13–29 hours single-threaded, ≈ 7–15 hours at 2 workers — plan §13 risk table acknowledges length but gives no wall-clock budget.
**Why it matters:** Operational planning (how long does Phase 1 take, when do gates happen) is impossible.
**Smallest plan change:** Add wall-clock estimates to §13 (scrape ≈ 1–2 days at 2 workers; enrichment ≈ 1–2 hours at 4/s), disk estimate (<1 GB), and a Phase 0/1 calendar with gate dates. One paragraph.

---

## P2 — hygiene / underspecified

### P2-1. Doubling-count / impression semantics undefined
**Evidence:** §8-3 "impressions only from render-confirmed events" and "reset on interaction" — no definition of render confirmation (client scroll event? Intersection Observer?) nor what "interaction" resets (any click? rating? hide?). §8-4 dedupe per (user, film, 12 h) — no table schema, no retention job code (`prune_impressions.py` is listed in §11-4 but unwritten).
**Smallest plan change:** Define schema + exactly-once submission in §8-4; specify client sends impressions on `IntersectionObserver` "visible" with `shown_at`; server dedupes; prune job deletes >90 days.

### P2-2. OpenAPI docs exposed; auth routes give no token yet
**Evidence:** FastAPI default `/docs` enabled (run.md:22); `profile/login` returns no token (§0-17, plan §10-4 "additive option" — not implemented). `/movies/tmdb-search` (`routers/movies.py:153-157`) is unauthenticated — an open TMDB proxy.
**Smallest plan change:** Gate `/docs` behind localhost or disable in production config; add rate limiting to `tmdb-search`; keep token path additive per §10-4.

### P2-3. `schema.sql` is labeled "NOT USED" but still present; `seed.py` seeds catalog on first run (~1,200 TMDB calls)
**Evidence:** `schema.sql:2-8`; `README.md:46-49`. Minor: confusing for new contributors; seed.py run makes unthrottled TMDB calls.
**Smallest plan change:** Delete or archive `schema.sql`; note seed.py throttling in run.md.

---

## Plan-critical behaviors that are UNTESTED today

`test_engine.py` (43 tests) covers: taste vector, genre affinity, match percent bounds; collab similarity/prediction/confidence; sentiment; signup/login policy and throttling; clip_cosine / decode; anchor detection; LSA signature; CF cache invalidation; MMR; watched upsert/backfill; search honesty (garbage queries); TMDB error distinction.

**Untested plan-critical behaviors:** end-to-end feed ranking (`rank_feed` never invoked in tests); `set_user_rating` / `ensure_cached_movie` write path; ZIP import; migration execution; IMDb/slug resolution; themes canonicalization; `EmbeddingStore` (doesn't exist); sparse CF / scipy path (doesn't exist); calibration; feedback/impressions endpoints; rate-watched / onboarding endpoints; token auth; `X-Username` impersonation guard; concurrent rebuild safety; latency benchmarks (`bench.py` doesn't exist); search exact-title-first behavior; watched-unrated invisibility fix.

Coverage is ~40% of the plan's critical path.

---

## (a) The 5 most serious disagreements with the plan

1. **The security mitigation is vapor.** `LOCAL_TRUST_HEADER` and the optional token session do not exist; `X-Username` fully impersonates any account including seed users (who have no passwords). The plan treats "local-only" as a substitute for auth, but `start.sh` relies on uvicorn's default binding and any local process can spoof the header. Fix before exposing anything beyond single-user dev.
2. **TMDB rate assumption will break the pipeline.** §6.2's 8–10 req/s exceeds TMDB's ≈4/s (40/10s); only 429 is backed off, not 422. Phases 2–3 will fail in production; request budgets and wall-clock estimates are wrong.
3. **Background rebuilds on SQLite have no threading model.** `check_same_thread=False` ≠ thread-safe sessions; no worker `SessionLocal`, no artifact-swap protocol, no WAL/busy_timeout. The "no rebuild in request path" guarantee is not implementable as described.
4. **Evaluation measures power users, not real users.** Warm personas are prolific seed raters; the cold ladder is edge-level holdout that leaks through the item-similarity matrix; new users get uncalibrated scores. The plan's central validity claim (ablation lift on held-out seeds) does not establish a good feed for a first-time user.
5. **The feedback loop is presented as existing but is zero code.** `user_feedback`/`feed_impressions` tables, all three endpoints, and frontend impression posting are absent; fatigue semantics are undefined. The plan's G7 funnel test ("feedback changes the next feed") cannot pass without building the whole loop plus frontend work.

## (b) Claims I can positively confirm

- Baseline numbers: 2,291 seed ratings; 380/1,904 films ≥2 ratings; 770 zero-rated; 287/1,281 CF-supported for mustafa (plan said 286/1,280); mustafa–seed overlap 89/623 — all match the DB exactly.
- Corruption mechanism: `rated-(\d)` in `scripts/import_letterboxd_profiles.py:25,56` converts `rated-10` (5★) to 0.5★; DB confirms zero seed-user 5.0 ratings.
- Search excludes the exact anchor film (`search.py:178-179`); watched-unrated films are excluded from feed (`movies.py:46-55`); cinephile has 588 watched-with-TMDB films and 0 ratings — all invisible.
- `movie_cache` has no `imdb_id` or `vote_count`; `tmdb.py` never stores them; no feedback/impressions/watched-unrated/onboarding endpoints exist anywhere.
- scipy 1.18.1 and sentence-transformers import in the venv — the proposed sparse-CF stack is technically viable.
- uvicorn defaults to 127.0.0.1; 43 tests exist as the plan states.
- Themes JSON: 598 entries, 591 themed, 108 labels, 0 genre-label overlap, title-keyed, 587/598 match current cache titles, but "The Odyssey" is ambiguous (3 cache rows).

## (c) Verdict

The plan is **not executable as written**. Phases 0–1 (audit, evaluation harness, re-scraping with the fixed `rated-(\d{1,2})` parser) are well-scoped and the baseline numbers check out. But Phases 4–8 require substantial unwritten code — `EmbeddingStore`, sparse scipy CF, background model artifacts with a safe SQLite threading model, all Phase 3 migrations, ranking v2 + calibration, the entire feedback/impressions loop with frontend posting, search→log exact-title handling, rate-watched/onboarding endpoints, and the token-auth path — and two of its operational assumptions (TMDB 8–10 req/s; background rebuilds on SQLite with no worker protocol) will fail in production. Its evaluation design measures seed power-users and cannot validate a real new-user experience. Re-scope: treat the plan as a sound roadmap with correct baseline facts, but rewrite §4–§8 with concrete code ownership, rate limits, threading protocol, migration DDL, and a new-user evaluation arm before execution.
