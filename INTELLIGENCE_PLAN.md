# CineMatch Intelligence Plan v2.2 — seeds, themes, ranking, feedback

Status: **in execution — Phase 0 underway (baseline infrastructure done; evaluation harness next).** All decisions confirmed 2026-10-19 (§14). v2.1 was cross-checked against an adversarial subagent review (`REVIEW_OF_INTELLIGENCE_PLAN.md`) with independent re-verification (§0.1).

Constraint: no commits; mustafa data untouched (623 ratings + password hash verified); API additive; backend stays local-only; token auth deferred by decision.

Changes from v2: security hardening moved into Phase 0; explicit fold-leakage protocol for evaluation; worker/WAL/artifact-swap protocol for model rebuilds; TMDB pacing revised per official docs (reviewer's 4/s claim refuted, retry gap confirmed); migration DDL appendix; token-budget rule for theme embeddings; feedback loop marked net-new; new-user evaluation arm; wall-clock/disk estimates; plan-critical test list.

---

## 0. Baseline (unchanged facts)

- 20 `lb_*` seeds, 2,291 ratings, 31% corrupted (5★ stored as 0.5★ by `rated-(\d)`), top-120-only scraping.
- Coverage: 380/1,904 films with ≥2 ratings; 770 zero; mustafa overlap 89/623; CF-supported feed candidates 287/1,281 (mustafa).
- Themes: 598 films, 591 themed, 108 labels; keyed by title; not used in scoring/embeddings.
- Ranking: `0.85·content + 0.15·raw vote_average`; item-item CF positive-only, O(n²) rebuild inside the request path on every rating change; no calibration, people, themes, context, or feedback.
- Search: exact titles excluded by anchor mode; watched-unrated films unreachable (cinephile: 588 watched, all invisible); ZIP import blocks the event loop.
- Auth: `X-Username` header only; `/profile/login` returns no token; seeds have no passwords (verified: 20/20 `password_hash IS NULL`).
- Letterboxd mechanics verified: `Fetcher` works; grid items expose `data-item-name`, `data-item-slug`, `data-item-link`; ratings classes `rated-1…rated-10`; 66–69 items/page; deep pagination; film pages expose themes; `letterboxd.com/imdb/<imdb_id>/` redirects; Letterboxd search page is JS-driven.

**Current state after Phase 0 cleanup (2026-10-19):** cinephile deleted (599 watched rows removed); mustafa is the default user; the 20 `lb_*` seeds are flagged `is_seed` and refuse header auth; 21 users / 2,914 ratings; backup + invariants in `/tmp/opencode/cinematch_phase0/`; baseline reports `backend/reports/seed_audit_pre_phase0.json`, `seed_audit_post_cleanup.json`, `bench_baseline.json`.

## 0.1 Cross-check outcomes (adversarial review + independent verification)

| Claim | Method | Result |
|---|---|---|
| TMDB allows only ~4 req/s and returns HTTP 422; enrichments will fail | Fetched `developer.themoviedb.org/docs/rate-limiting`; 12-request probe; read `tmdb.py:29-38` | **REFUTED.** Docs: legacy 40 req/10 s limit disabled since 2019; current upper limit "in the 40 requests per second range"; must respect `429`. Probe: 12/12 HTTP 200. **Real gap confirmed:** `_get` has zero retry/backoff (raises on any non-200). Plan keeps 8 req/s + adds retry/backoff. |
| Themes have no slugs anywhere; title-keyed only | Read `browser_scraped_themes.json` | **PARTLY REFUTED.** Entries are `{year, url, genres[], themes[]}` — the `url` yields the slug for all 598. DB has no slug column (true). Ambiguity is real: "The Odyssey" exists 3× (2016, 2026, 2026). Max 14 themes/film, mean 6.7 → token budget matters. |
| MiniLM truncates at 256 wordpieces | Measured in venv | **CONFIRMED.** `max_seq_length=256`; 300-word input → 302 wordpieces. |
| Background rebuilds have no threading story | Read `database.py:33-36`, `collab.py:141-197` | **CONFIRMED.** `journal_mode=delete`; no WAL, no `busy_timeout`; `check_same_thread=False` only; `_pred_cache` holds one entry; rebuild runs in the request path. Protocol now explicit (§7). |
| ZIP import is a blocking DoS; exact-title search suppressed; watched-unrated invisible; no feedback/impressions code | Code reads | **CONFIRMED** (all previously known; now itemized per file in `REVIEW_OF_INTELLIGENCE_PLAN.md`). |
| Phase 3 migrations don't exist | `database.py:351-358` | **CONFIRMED.** `_add_missing_columns` adds only `display_name`, `password_hash`. DDL now in Appendix A. |
| Seed accounts fully impersonable | `deps.py:16-32`, DB | **CONFIRMED, now fixed.** Header alone returned any user; seeds have no password. Phase 0 added `is_seed` + a 403 guard. |
| 43 tests, ~40% of plan-critical path untested | `test_engine.py` | **CONFIRMED.** Test list in §15. |

---

## 1. Principles

1. **Evaluation-first.** No algorithm or data feature is kept without a measured lift on a held-out split. Gates are set after the baseline.
2. **Evidence-gated complexity.** Each added signal must win an ablation.
3. **No lost negatives.** Seed data includes the low tail and watched history; dislike direction is first-class.
4. **No unverified operational assumptions.** Rate limits, truncation, threading, and payload costs are measured or tested before the phase that depends on them.
5. **Deterministic, resumable, idempotent** pipelines; manifests and audits for every batch; backups and tested rollback.
6. **Latency is a feature.** No full-row loads or matrix rebuilds in request paths; budgets enforced by benchmarks.
7. **Honest UI.** Match percentages are calibrated probabilities or percentiles; reasons gated by real support.
8. **Local-only until auth.** No exposure beyond localhost until token sessions and seed lockout exist.
9. **Additive API.** Legacy fields (`mode`, `reference_title`, `score`, `reason`, `results`) keep working.

---

## 2. Sequencing

```
Phase 0  Baseline + eval harness + personas + security hardening   → G0: gates set
Phase 1  Seed harvesting (fresh cohort, full histories + likes)     → G1: clean data
Phase 2  Slug/IMDb resolution + idempotent import                  → G2: audit passes
Phase 3  Metadata/enrichment + embeddings v2 + migrations          → G3: coverage + embed ok
Phase 4  Scale architecture (store, sparse sims, async rebuilds)   → G4: latency budgets
Phase 5  Ranking v2 + calibration + feedback + exploration         → G5: ablation lift
Phase 6  Evaluation v2 + tuning (incl. new-user arm)               → G6: holdout report
Phase 7  UX P0 (search/log, rate-watched, feedback, ZIP fix)       → G7: funnel works
Phase 8  Rollout + ops                                             → G8: invariants + rollback
```

Phase 7 can run in parallel with Phases 4–6 (different files) but must land before rollout.

---

## 3. Phase 0 — Baseline, evaluation harness, personas, hardening

1. **Safety snapshot — done**: online backup + invariants at `/tmp/opencode/cinematch_phase0/cinematch_pre_phase0_20261019.db` (sha256 `d6e23fc5…`); mustafa 623 ratings/logged + hash verified after cleanup.
2. **`scripts/seed_audit.py`**: per-user counts, rating histograms, low-tail share, coverage ≥1/≥2/≥5, zero-rating share, duplicate titles, mustafa overlap, CF quantiles. JSON + CLI table.
3. **`scripts/eval_recommender.py` v0**:
   - relevance: graded (stars) and binary (≥ `max(3.5, user_mean)`), reported separately;
   - candidates: catalog minus the persona's **rated + watched** films;
   - **split protocol (primary = user-level):** for each persona fold, remove the entire persona from the ratings matrix and **rebuild item sims, taste vector, theme/person/franchise affinities from the reduced data** before scoring. Near-duplicate/franchise clusters (e.g. "The Odyssey" ×3) are collapsed in candidate scoring.
   - **cold ladder (edge-level, supplementary):** 0/5/10/25/50 visible ratings; labeled leakage-possible (held-out items remain in other users' ratings), never used as the sole gate.
   - **new-user arm:** 0 ratings → cold-start + onboarding feed, scored against that persona's eventual full history.
   - metrics: Recall@20/50, NDCG@20, MRR, MAE, catalog coverage, popularity bias, intra-list similarity, CF-support share.
4. **Security hardening — done:**
   - `is_seed` column + migration; `resolve_user` returns 403 for seed accounts; seeds have no passwords and are never login-capable;
   - `DEFAULT_USERNAME = "mustafa"`; cinephile deleted (599 watched rows); stale frontend `cinephile` sessions clear themselves;
   - token sessions deferred by decision #3; `start.sh` binds 127.0.0.1; `/docs` gating remains a Phase 7 task.
5. **`scripts/bench.py`**: feed/search/engine-status latency at current size and simulated 3k/6k/10k.
6. **G0 gate**: baseline recorded; provisional bands (§12) confirmed or adjusted.

Deliverables: `backend/reports/baseline-<ts>.json`, persona JSON, bench numbers, `REVIEW_OF_INTELLIGENCE_PLAN.md` findings triaged.

---

## 4. Phase 1 — Seed harvesting v2

### 4.1 Cohort selection (`scripts/letterboxd_handles.py`)
- Sources: `/members/`, `/members/popular/...`, following lists of known users, popular-review pages of catalog films, and reviewers discovered on film pages — per the user's method.
- Pre-screen: parse profile stats (films logged); require ≥100 rated films and a first-page taste sample.
- Quotas: 40% mainstream/high-volume, 30% arthouse/critic, 20% non-English-first, 10% overlap-optimized. Per-source caps.
- Output `letterboxd_data/seeds/handles.json` + pseudonym mapping `seeds/handles_private.json` (`handle` ↔ `lb_<sha1[:10]>`).

### 4.2 Profile scraper (`scripts/scrape_letterboxd_profiles.py`)
- **Fresh cohort replaces the corrupt 20**: build 80–100 new clean seeds; the old `lb_*` rows are retired once the new set passes G2 (no refresh-in-place, decision #6).
- Full history by default: paginate `/<user>/films/by/rating/`; safety cap 2,000 items; if capped, stratified sample (first/last + random middle), `sampled=true`.
- Parse: `data-item-name` → title/year, `data-item-slug`/`data-item-link` → slug, `span.rating` class `rated-(\d{1,2})` → stars = n/2.
- **Likes are a separate scrape**: `/<user>/likes/films/` → `liked: true` per (user, film), stored independently from stars (a 5★ rating and a like are different signals); likes count as strong positives in ranking and as binary relevance in evaluation.
- Watched grids for 30 evaluation personas (rated + unrated) so evaluation excludes seen films.
- Rate limiting: 1 req / 1.2–2.2 s per worker, max 2 workers, jitter; stop-on-challenge with saved state; per-user resume.
- Output `seeds/raw/<pseudonym>.jsonl` + `seeds/watched/<pseudonym>.jsonl`.
- Validators per user: values ⊆ {0.5…5.0}; ≥100 entries; ≥3 distinct values; low tail (≤2.5) present for profiles with ≥200 ratings; **5.0 bucket present** (the exact corruption marker); no duplicate slugs; quarantine with reasons.
- Fixture tests: `rated-1…rated-10`, missing rating span, parenthesized titles/years, pagination, resume, challenge detection.

### 4.3 G1 gate
80–100 new valid profiles (old corrupt cohort retired after this passes); ≥30k seed ratings; ≥95% raw entries valid; zero parser mismatches on fixtures; **zero corruption markers across all seed users**; likes captured for every seed.

---

## 5. Phase 2 — Resolution and import

### 5.1 Resolution (`scripts/resolve_seed_ratings.py`)
- Every scraped entry has a slug → get IMDb id from the film page (cached), resolve via TMDB `/find/<imdb_id>` (exact).
- Films without IMDb: strict title+year search with margin-based acceptance; ambiguous → `seeds/unresolved.csv`.
- **Pacing**: token bucket 8 req/s; retry with jittered backoff on 429/5xx, honor `Retry-After`; checkpoint **per film**; persistent failure queue.
- **Budget**: ~5k unique films × (1 LB page + 1 TMDB find) ≈ 10k calls ≈ 25–40 min at 8/s incl. retries.
- Upsert catalog additively; fill poster/overview/genres/year; store `imdb_id` from the existing `get_movie_full` response (`tmdb.py:262` already fetches it, never stored).

### 5.2 Import (`scripts/import_seed_profiles.py`)
- FK-safe replace per seed user (ratings, likes, watched in dependency order; keep user rows); idempotent upsert per (user, film); one transaction per user; manifest counts.
- mustafa excluded by hard guard asserted before/after; pseudonyms only.

### 5.3 G2 gate
`seed_audit` passes: ≥30k ratings; films ≥2 ratings ≥ 2,000 (validate/adjust from dry run); low-tail share in expected range; zero corruption markers; unresolved list reviewed; tests green.

---

## 6. Phase 3 — Enrichment, schema, embeddings

### 6.1 Letterboxd themes (`scripts/scrape_letterboxd_films.py`)
- The existing 598 JSON entries carry `url` → slug + genres; reuse them; scrape the rest of the catalog by slug/IMDb redirect.
- **Key by `tmdb_id`, never by title**; ambiguous titles resolved by slug/year ("The Odyssey" 2016 vs 2026×2).
- Canonical theme ids from Labelboxd theme slugs; display label preserved; `theme` weight 1.0, `mini-theme` 0.7; dedupe against genres; TMDB keywords fallback with stoplist.
- Degrade gracefully: films without themes simply skip `theme_match` (no per-user support gate blocks the rest of the score).
- Checkpointed JSONL; 1.5–2.5 s throttle, ≤2 workers.

### 6.2 TMDB enrichment (`scripts/enrich_tmdb_catalog.py`)
- `movie/{id}?append_to_response=credits,keywords,external_ids` → runtime, original_language, vote_count, collection, imdb_id, keywords, directors, top-8 cast.
- `film_people`, `film_related` tables; 8 req/s token bucket; retry on 429/5xx + `Retry-After`; per-film checkpoints; ~5k calls ≈ 15–25 min.

### 6.3 Schema + migrations
- Additive columns/tables in **Appendix A**; implemented in `_add_missing_columns` (explicit `ALTER TABLE ... ADD COLUMN`, guarded by `PRAGMA table_info`) + `create_all` for new tables.
- Migration test applies the DDL to a copy of the live DB, runs twice (idempotency), asserts columns/tables.

### 6.4 Embeddings v2
- Text order: `Title (year). Genres: …. Themes: …. Overview: <truncated>` — genres/themes before the truncation point.
- Budget: title + ≤3 genres + ≤8 themes + overview truncated to ~80 words; hard assertion **≤250 wordpieces** using the longest catalog text; ellipsis on truncation.
- `EMBED_TEXT_VERSION` participates in the provider signature → one full re-embed.
- `EmbeddingStore` loads `(tmdb_id, vector)` in one query into a float32 matrix; per-row JSON decode only as fallback.
- LSA fallback guard: fail loudly if the active provider is LSA at >2k catalog.
- Verify 100% non-null, dim 384, batch timing recorded.

G3 gate: ≥90% of recommendable films themed or keyword-covered; migrations idempotent; embeddings coverage 100%.

---

## 7. Phase 4 — Scale architecture (P0, before ranking v2)

1. Request paths never load full rows with embeddings: `load_only` columns or `EmbeddingStore`; `engine-status` from cached counts. (Measured: decoding 1,904 embeddings = 0.07 s → ~0.35 s at 10k, over budget.)
2. Sparse CF: `scipy.sparse` centered matrix; co-count ≥2; top-50 neighbors/film; user-KNN top-30; SVD k=48 optional.
3. **Background rebuild protocol (explicit):**
   - enable `PRAGMA journal_mode=WAL` and `busy_timeout=30000` at engine creation;
   - the rebuild worker opens its **own short-lived `SessionLocal`** (sessions are not thread-safe even with `check_same_thread=False`);
   - worker builds in memory, writes artifacts to `model_store/model_<fingerprint>.npz.tmp`, then `os.replace` (atomic);
   - the served model is swapped under `_model_lock`; the worker **never writes served DB tables**;
   - requests serve the last good model while a new one builds (stale-while-revalidate); failed builds keep the old model.
4. Per-user prediction LRU (cap 8); `matrix_version` uses count + `MAX(rated_at)` + sum.
5. Decoded embedding arrays are documented read-only; no mutation in scoring paths.
6. Eligibility: `vote_count ≥ 50` or `popularity ≥ threshold`; exclude hidden/watched/rated; retrieval caps top-N before full scoring above 6k.
7. Batched `embed_movies` for imports; `ensure_cached_movie` no longer encodes inline in bulk mode.
8. **Concurrency test**: rebuild thread + parallel feed/search requests; assert no `database is locked`, no stale reads after swap.
9. Benchmarks gate G4: feed ≤250 ms, search ≤150 ms at 10k simulated candidates.

---

## 8. Phase 5 — Ranking v2, calibration, feedback, exploration

### 8.1 Scoring (config in `services/ranker_config.py`)
Retrieval → scoring → MMR → caps. Terms (all bounded, support-gated):

| Term | Source | Gate |
|---|---|---|
| content | taste vector cosine (pos+neg, sentiment) | any rating |
| liked | explicit Letterboxd like (separate signal) | any like; strong-positive bonus |
| theme_match | theme affinity × film themes | ≥5 themed ratings |
| genre_match | signed genre affinity, shrunk | ≥3 ratings/genre |
| cf_item | item-item, signed sims, support-shrunk | co-count ≥2 |
| cf_user | user-KNN (k=10–30) | ≥5 co-rated |
| cf_latent | SVD reconstruction | ablation-only |
| person | director/writer/top-cast affinity | ≥2 films/person |
| franchise | collection affinity | ≥1 rated entry |
| quality | Bayesian `(vR+mC)/(v+m)` | vote_count |
| popularity | log-scaled | cold start/tie-break |
| era/lang/runtime | user affinities | ≥5 supporting ratings |
| recency | half-life on `rated_at` | **real users only** (seed timestamps = import time) |
| dislike_penalty | negative centroid + hidden feedback | ≥5 disliked |
| novelty/freshness | impressions + session rotation | — |
| rule_boosts | feedback events, 60-day half-life | explicit |

Dynamic blending by per-candidate confidence and per-user support; cold users use content + smoothed quality + exploration. Ablation ladder keeps only winners.

### 8.2 Calibration
- Per-user isotonic/logistic mapping raw → P(rating ≥ user threshold) only for users with ≥30 ratings; global fallback below that.
- `match` shows the calibrated probability when available, otherwise a percentile; predicted-star text only when holdout MAE is below threshold.
- Reasons gated: "raters with overlapping taste" requires calibrated confidence + support.

### 8.3 Rulebook (bounded, decayed, explainable)
- hide/unhide and "not interested" propagate to film/themes/director/cast;
- era/language affinity learned, never hardcoded;
- exploration slot: 1 in 12 items from a content-adjacent lower-confidence band, deterministic per session seed;
- diversity caps: ≤2/director, ≤3/franchise per 20; MMR λ 0.72–0.80;
- fatigue: impressions counted only from render-confirmed events; penalty `0.9^impressions` floor 0.6 **applied at retrieval**; reset on rating/hide.

### 8.4 Feedback data (net-new: tables + endpoints + frontend)
- `user_feedback(user_id, tmdb_id, kind, created_at)` unique per (user, film, kind);
- `feed_impressions(user_id, tmdb_id, session_id, shown_at)`; client posts on `IntersectionObserver` visible; server dedupes per (user, film, 12 h); retention 90 days (`prune_impressions.py`);
- endpoints: `POST/DELETE /movies/{id}/feedback`, `GET /movies/hidden`, `POST /movies/impressions` (batched);
- G7's "feedback changes the next feed" requires all of the above plus frontend posting — treated as real scope, not wiring.

G5 gate: each retained signal wins its ablation (≥ +2% NDCG@20, baseline set at G0) without coverage/diversity loss; calibration MAE below threshold; no latency regression.

---

## 9. Phase 6 — Evaluation v2 and tuning

- Arms: warm user-level holdout; cold ladder (supplementary, leakage-labeled); **brand-new user** (0 ratings → cold-start + onboarding, scored against eventual history); cohort splits (mainstream/arthouse, language); seed-vs-real comparison.
- Splits: tuning split for weights; separate reporting split; leakage tests enforced (per-fold model rebuild, franchise dedupe).
- `scripts/tune_ranker.py`: bounded coordinate ascent on NDCG@20 subject to coverage/diversity floors; ≤2 rounds; exports `config/ranker_weights.json`.
- Reports: markdown + JSON per user/aggregate with popularity-bias and calibration tables.
- G6 gate: report reviewed; §12 bands met or exceptions documented.

---

## 10. Phase 7 — UX hooks P0 (parallel with 4–6)

1. **Search-to-log fix (new code):**
   - prepend an exact `match_type: "title"` result with `reference` (`tmdb_id`, year, poster); "more like this" anchor neighbors render directly below it;
   - parse `"Title (YYYY)"` / `"Title YYYY"`; duplicates disambiguated by year; short titles exact-only; mood only when no exact match; keep `mode`/`reference_title`;
   - no catalog match → merge `tmdb.search_movies` results marked `external: true`;
   - badges for rated/watched; hidden filtered; frontend gets a "Log this film" action on the exact result.
2. **Rate-your-watched queue**: `GET /movies/watched-unrated?limit&offset` (rated excluded, watched-not-rated included); `GET /movies/onboarding` (24 diverse picks); LogForm save triggers feed refetch.
3. **Minimal UI**: heart/✕ on cards, hidden list, reasons from `signals`.
4. **Importer hygiene**: `run_in_threadpool` or background job + polling; 50 MB cap; zip-bomb checks (entry count, total uncompressed size); strict resolution; UI timeout.
5. **Auth**: keep `X-Username` local behind `LOCAL_TRUST_HEADER`; additive HMAC token sessions; seed accounts never login-capable; no exposure beyond localhost without the flag off.

G7 gate: manual funnel test — exact-title search → log → feed adjusts; feedback changes next feed; no server freeze during ZIP import.

---

## 11. Phase 8 — Rollout and ops

1. Dry run on the `/tmp` copy: import → enrich → embed → eval → funnel.
2. Live run with server stopped: backup → import → enrich → embed → restart venv backend → verify.
3. Verify: audit gates, eval report, benches, invariants (mustafa 623 + hash unchanged; cinephile already removed), rollback script `scripts/restore_db.sh <backup>` tested.
4. Ops: `scripts/refresh_catalog.py` (TTL metadata/vote_count/popularity), `scripts/prune_impressions.py`, model artifacts in `backend/model_store/`, `run.md` runbook update.
5. Hygiene: archive `schema.sql` (labeled NOT USED), note `seed.py` throttling in `run.md`.
6. G8 gate: invariants + latency + rollback verified.

**Wall-clock estimates:** Phase 1 scrape ≈ 1–2 h at 2 workers (≈80 profiles × ~25 pages, 1.2–2.2 s/req); Phase 2 resolution ≈ 25–40 min; Phase 3 ≈ 40–80 min (themes re-scrape ~1 h dominant). Disk: <1 GB (10k embeddings ≈ 35 MB text + artifacts).

---

## 12. Acceptance gates (provisional; final values at G0)

| Gate | Target |
|---|---|
| Data | 80–100 new valid profiles (old cohort retired after audit); ≥30k ratings; ≥95% parse validity; low tail + likes present; zero 5★→0.5 artifacts across all seeds |
| Coverage | films ≥2 ratings ≥ 2,000 (validate/adjust after dry run); mustafa overlap ≥150/623 or +50% relative |
| Themes | ≥90% recommendable films themed or keyword-covered; keyed by tmdb_id |
| Scale | feed ≤250 ms, search ≤150 ms at 10k; no rebuild in request path; concurrency test green |
| Ranking | each retained signal wins its ablation; NDCG@20 lift set at G0 (provisional band +15–30% relative) |
| Calibration | MAE ≤0.75★; match% = calibrated probability where eligible, percentile otherwise |
| Funnel | search→log works for exact, year-suffixed, duplicate, and non-catalog titles |
| Integrity | mustafa invariants (623 ratings + hash); seeds locked out; cinephile removed; rollback tested; 46 tests + new suites green; migration test idempotent |

---

## 13. Risks (updated)

| Risk | Mitigation |
|---|---|
| Full-history scrape length | 2 workers, resume, 2k cap + stratified sampling; 80–100 profiles, 1.5–2.5 h budget |
| Cloudflare/challenge | stop-on-challenge, saved state; stealth fallback only if needed |
| TMDB 429/5xx | 8 req/s token bucket (official upper bound ~40/s), retry + jitter + `Retry-After`; mock test |
| Resolution misses (foreign/indie) | slug + IMDb-first; strict scoring; reviewed unresolved list |
| Seed privacy/ToS | pseudonymization, local mapping, low volume, no redistribution |
| Model rebuild cost | background builds + persisted artifacts + atomic swap + stale-while-revalidate |
| SQLite locking | WAL + busy_timeout + worker sessions + single-writer discipline |
| Request-path payload | EmbeddingStore + `load_only` + retrieval caps; budgets in G4 |
| Eval leakage | per-fold full model rebuild; cold ladder labeled supplementary; franchise dedupe |
| Overfitting | evaluation-first, ablation gates, bounded tuner, separate reporting split |
| Filter bubble | exploration slot, caps, diversity metrics, hide/unhide |
| Impressions noise | render-confirmed events (IntersectionObserver), session dedupe, 90-day retention |
| Auth absence | localhost binding explicit; seed lockout; token sessions before any exposure |
| Metadata staleness | TTL refresh job; `cached_at` |
| SQLite single writer | server stopped for batches; WAL; rollback requires restart |

---

## 14. Confirmed decisions (2026-10-19)

1. **Full histories** for new seeds, 2,000-item cap; discover candidates from reviews and profile stats.
2. **Search fix P0**: exact film first, "more like this" underneath.
3. **Token auth deferred**; local-only for now; seed accounts already locked out.
4. **Likes are separate**: scrape `/<user>/likes/films/`; never equate a like with a 5★ rating.
5. **Minimal UI hooks** now; redesign deferred.
6. **Fresh cohort, no refresh**: 80–100 new seeds replace the old 20 after audit; no re-harvest-in-place.
7. **Themes for all** seed-catalog films, keyword fallback.
8. **Seeds are data only**: `is_seed=1`, no login; mustafa is the only real account.
9. **TMDB 8 req/s + retry/backoff** for bulk scripts; interactive requests are not paced.
10. **cinephile deleted**; default user is mustafa; mustafa's 623 ratings + password hash unchanged.

---

## 15. Deliverables and tests

**Scripts:** `seed_audit` (done), `bench` (done), `eval_recommender`, `letterboxd_handles`, `scrape_letterboxd_profiles`, `resolve_seed_ratings`, `import_seed_profiles`, `scrape_letterboxd_films`, `enrich_tmdb_catalog`, `tune_ranker`, `refresh_catalog`, `prune_impressions`, `restore_db`.

**Phase 0 progress (2026-10-19):** backup `sha256 d6e23fc5…`; pre/post audits written; bench baseline (`feed p50 90 ms / p95 104 ms`, `search p50 61 ms / p95 75 ms` at 1,904 films); cinephile removed; `is_seed` guard live; TMDB `_get` retries 429/5xx; 46 tests green. **Still open in Phase 0:** `eval_recommender.py` + persona definitions.

**Code:** EmbeddingStore; sparse CF + background rebuild protocol; ranker v2 + config + calibration; feedback/impressions endpoints + tables; search exact-title fix; watched-unrated/onboarding endpoints; ZIP import hardening; migrations (Appendix A); `_get` retry/backoff (done); seed lockout (done).

**New tests (beyond the 43):** parser fixtures (`rated-10`), slug/IMDb resolution, theme canonicalization + tmdb_id keying, migration idempotency on DB copy, `EmbeddingStore` load + budget assertion (≤250 wordpieces, themes retained), sparse-CF correctness, rebuild concurrency (no locked DB), `_get` retry mock, calibration gates, feedback/impression dedupe, search exact-title-first + `external`, watched-unrated/onboarding, impersonation guard, benchmarks with hard fail.

---

## Appendix A — Migration DDL (Phase 3)

`movie_cache` add columns (guarded by `PRAGMA table_info`):
`themes TEXT` (JSON list of `{id,label,weight}`), `keywords TEXT`, `runtime INTEGER`, `original_language TEXT`, `vote_count INTEGER`, `collection_id INTEGER`, `imdb_id TEXT`, `letterboxd_slug TEXT`, `text_version TEXT`.

`users` add: `is_seed INTEGER NOT NULL DEFAULT 0`.

New tables (`CREATE TABLE IF NOT EXISTS`):
- `film_people(id, tmdb_id, person_id, name, role, ordering, source)` + index `tmdb_id`
- `film_related(id, tmdb_id, related_tmdb_id, source, rank)` + index `tmdb_id`
- `user_feedback(id, user_id, tmdb_id, kind, created_at, UNIQUE(user_id, tmdb_id, kind))`
- `user_likes(id, user_id, tmdb_id, liked_at, UNIQUE(user_id, tmdb_id))` + index `(tmdb_id)`
- `feed_impressions(id, user_id, tmdb_id, session_id, shown_at)` + index `(user_id, shown_at)`
- `seed_profile(id, user_id, cohort, handle_hash, sampled, n_items, imported_at)`

Rollback: the pre-migration backup file + `scripts/restore_db.sh`.
