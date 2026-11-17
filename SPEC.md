# CineMatch — Project Specification (v3)
> Reflects the implemented system. Every decision here is already made and built.

---

## 0. What This App Is

CineMatch is a multi-user movie recommendation web app with a measured ML engine:

1. **Ranking v2** — content taste + themes + collection affinity + TMDB quality,
   with confidence-scaled item-item CF (max 0.35), then MMR reranking (λ=0.70).
   Direct likes are separate from star ratings and feed affinity/suppression.
2. **Mode-aware semantic search** — anchor ("something like Interstellar"), mood
   ("I want to cry"), or general natural-language search. A bare exact title is
   returned first in its own block, with "More like this" underneath.
3. **Letterboxd-style logging** — half-star ratings on a **0.5–5.0 scale**, optional
   VADER-scored reviews, and a zip import that feeds history into every model.
4. **Feedback** — explicit "Not interested" suppression and render-confirmed feed
   impressions, both scoped per user.
5. **Honesty by design** — `GET /movies/engine-status` reports the embedding backend,
   CF raters/ratings/pairs, and sentiment model. The offline evaluator, frozen
   baseline, ablation runs, and audit reports live in `backend/reports/`.

Identity: username + password (PBKDF2-SHA256; legacy hashes rehashed on login).
User-scoped routes take identity via the `X-Username` header. The 80 harvested
Letterboxd profiles are `is_seed=1` rows with no password and are rejected by both
login and the feed dependency.

---

## 1. Folder Structure

```
cinematch/
├── backend/
│   ├── main.py                  # FastAPI app, startup pipeline, /uploads mount
│   ├── seed.py                  # CLI development seeder (--pages N)
│   ├── run_server.py            # uvicorn runner used by start.sh
│   ├── smoke_test.py            # full-stack TestClient pass
│   ├── test_engine.py           # taste/CF/sentiment unit tests
│   ├── test_intelligence_v2.py  # ranking v2, feedback, themes, eval contracts
│   ├── test_seed_pipeline.py    # import/theme/audit contracts
│   ├── test_letterboxd_scraper.py
│   ├── models/database.py       # ORM + schema v2 + migrations + passwords
│   ├── services/
│   │   ├── tmdb.py              # TMDB client + metadata enrichment
│   │   ├── embeddings.py        # MiniLM provider, LSA fallback, re-embed logic
│   │   ├── collab.py            # item-item CF w/ shrinkage + confidence
│   │   ├── taste.py             # taste vector, genre affinity, match %
│   │   ├── features.py          # theme/genre/collection/liked user signals
│   │   ├── ranker_config.py     # central weights + CINEMATCH_DISABLE ablations
│   │   ├── ranking.py           # ranking v2 + MMR + explanations
│   │   ├── search.py            # exact/anchor/mood/general search
│   │   ├── feedback.py          # suppression + impression bookkeeping
│   │   ├── sentiment.py         # VADER wrapper + aggregation
│   │   ├── ratings.py           # set/remove rating (logs + CF matrix)
│   │   └── letterboxd.py        # CSV parsing, watched upserts, local sync
│   ├── routers/
│   │   ├── deps.py              # resolve_user (X-Username), format_movie
│   │   ├── movies.py            # feed/search/detail/engine-status
│   │   ├── feedback.py          # feedback + impressions
│   │   ├── logs.py              # per-user rating logs
│   │   ├── reviews.py           # review CRUD + POST /reviews/score
│   │   └── profile.py           # auth, profile CRUD, pfp, letterboxd import
│   ├── scripts/                 # harvest/import/theme/enrich/audit/eval/bench
│   ├── reports/                 # eval, benchmark, audit, theme artifacts
│   └── cinematch.db             # live clean cohort database
└── frontend/
    ├── package.json / vite.config.js / index.html
    └── src/
        ├── main.jsx / App.jsx   # routing + providers
        ├── styles/               # design-system tokens and base styles
        ├── hooks/api.js          # apiFetch/apiUpload + api object
        ├── components/           # Poster, Movie cards, Log modal, Stars, PageHeader…
        └── pages/                # HomeFeed, Search, Movie, Logs, Profile, Login…
```

---

## 2. Database Schema

SQLite at `backend/cinematch.db`; SQLAlchemy models in `models/database.py`.

- `users` — `user_id`, unique `username`, nullable `password_hash`,
  `is_seed` (0/1), `created_at`.
- `seed_profile` — provenance for seed users: `user_id`, `cohort`, `handle_hash`,
  `sampled`, `n_items`, `imported_at`. The raw handle mapping is private and is
  never stored in the app DB or exposed by the API.
- `user_profile` — display fields keyed by username.
- `movie_cache` — 20,514-film catalog: IDs, overview, genres, poster, year,
  popularity, vote average/count, runtime, language, collection, IMDb/Letterboxd
  identifiers, `themes` JSON, `keywords` JSON, `text_version`, `embedding` JSON.
- `logged_movies` — per-user star ratings (0.5–5.0), unique per user/film.
- `watched_movies` — Letterboxd watch history, including unresolved titles.
- `user_likes` — binary likes, deliberately separate from the rating matrix.
- `user_feedback` — `hide` / `not_interested` rows; suppresses candidates.
- `feed_impressions` — rendered feed rows, deduped per user/film/6-hour window.
- `reviews` — one per user/film, with stored VADER scores.
- `films` + `ratings` — CF dimension and matrix; `films.film_id == tmdb_id`.
- `film_people` / `film_related` — enriched TMDB credits/recommendations.
- `letterboxd_popularity` — top-20,000 import ledger with resolution status.
- `theme_clusters`, `film_subniches` — theme metadata, display only.
- `film_neighbors` — `behavior_v1` local behavior edges plus `hybrid_v1` cold-start fallback.
- `behavior_clusters`, `film_behavior_subniches` — experimental local behavior communities (not exposed).
- `app_meta` — key/value store; holds the embedding signature.

Legacy single-user tables are migrated in place on startup.

---

## 3. Engine Services

### embeddings.py
- `MiniLMProvider` (`all-MiniLM-L6-v2`, normalized) when torch loads, else
  `LSAProvider` (TF-IDF → TruncatedSVD → normalize).
- Text representation: `"{title}. {overview} Genres: …"`; the active signature is
  stored in `app_meta`, and a provider change triggers re-embedding.
- Catalog is fully embedded (20,514/20,514).

### taste.py
- Midpoint-centered rating weights around 3.0; disliked films push away at 0.35;
  review sentiment scales weights by `1 + 0.35·compound`; result is unit-normalized.
- `genre_affinity` returns interpretable loved/avoided lists; `match_percent`
  maps cosine to 0–100.

### collab.py
- Item-item cosine on mean-centered ratings, positive-only, shrunk:
  `sim · co_count / (co_count + 8)`; top-50 similar items cached, top-20 used per
  prediction.
- Prediction = user mean + Σ(sim·centered)/Σ|sim|; confidence =
  `min(1, Σ|sim| / 3)`.
- Dormant with fewer than 2 raters; `cf_state` powers engine-status.

### features.py (ranking v2 signals)
- Themes: support-gated at 5 themed/liked films; affinity normalized to [-1, 1].
- Genres: support-gated per genre (3 ratings); **weight 0 in final v2** because
  full-coverage themes made the signal redundant.
- Collection affinity from rated/liked films in the same TMDB collection.
- Likes: positive-only centroid and consumed-film suppression; direct liked
  weight is 0 because it hurt NDCG.

### ranking.py / ranker_config.py
- Final weights: content **0.48**, liked **0.00**, theme **0.12**, genre **0.00**,
  collection **0.04**, quality **0.16**; CF max **0.35**, confidence-scaled;
  MMR λ **0.70**. Missing signals are removed and weights renormalized.
- Quality prior shrinks low-vote-count averages toward the catalog mean.
- Explanations are derived from the winning bounded signal (confident CF,
  theme/genre hit, collection, content proximity, quality).
- `CINEMATCH_DISABLE=liked,theme,genre,collection,cf` reproduces ablations.
- Cold start uses a quality × popularity score.

### search.py
- Exact title/year match is detected first and returned as a separate payload;
  similar results are rendered underneath.
- Anchor detection: fuzzy title ratio ≥0.82 plus "something like X" patterns;
  score blends semantic similarity and CF co-rating evidence.
- Mood: 8 interpretable genre/term profiles; no title lists.
- General: 0.60 semantic + 0.25 lexical + 0.15 quality + 0.12 taste nudge.

### feedback.py
- `hide` / `not_interested` rows are subtracted from feed candidates; removal is
  reversible and does not mutate scores invisibly.
- Impressions are recorded from the client after render, deduped per
  user/film/session in a 6-hour window; 90-day retention.

### sentiment.py
- Cached VADER analyzer; ±0.35 labels; aggregate distribution and representative
  quotes for the profile page.

---

## 4. API Surface

All user-scoped routes read identity from `X-Username` (`routers.deps.resolve_user`;
auto-provisions `cinephile` only when zero users exist, else 404). Seed users are
rejected with 403 on feed routes and 401 on login.

```
GET  /movies/feed?limit=18&exclude=1,2
                                 {feed_type, profile_summary, results[]};
                                 exclude is additive and keeps refresh deterministic
GET  /movies/search?q=…          {query, mode, score_label, reference_title,
                                  exact, results[]}
GET  /movies/tmdb-search?q=…     TMDB lookup for logging
GET  /people/{person_id}         filmography from catalog credits:
                                  {person_id, name, roles[], film_count, films[]}
GET  /movies/engine-status       embeddings, CF state, sentiment model
GET  /movies/profile-summary     taste summary, genres, sentiment
GET  /movies/{tmdb_id}           detail; auto-caches uncached TMDB ids

GET  /feedback                   hidden / not-interested list
POST /feedback/{tmdb_id}         {kind: hide|not_interested}
DEL  /feedback/{tmdb_id}         undo
POST /feedback/impressions       {tmdb_ids[], session_id?} → {recorded}

GET/POST/DELETE /logs/[/...]     per-user star ratings
POST /reviews/score              stateless vibe scoring
GET/POST/DELETE /reviews/[/...]  saved reviews with sentiment
GET/PUT /profile/                profile + stats
POST /profile/signup|login|signout
POST /profile/upload-pfp
POST /profile/import-letterboxd  ratings.csv (+watched.csv)
```

`format_movie` in `routers/deps.py` is the canonical movie JSON shape.

---

## 5. Frontend

React 18 + Vite; no state library, no CSS framework, plain JSX/CSS.

- **Design system** in `src/styles/`: warm theatre-black surfaces, marquee amber
  accent, Fraunces display serif, Space Grotesk body, JetBrains Mono labels;
  editorial page headers, poster cards, star inputs, vibe meter, modal system,
  skeletons, toasts, responsive breakpoints.
- **Auth gate**: first visit lands on Login; credentials persist in localStorage and
  travel as `X-Username`/`X-Display-Name`; signout clears only client identity.
- **Feed**: skeletons, cold-start banner, reasons, "Not interested" action, and a
  render-confirmed impression POST after cards appear.
- **Search**: exact-title block labelled "The film you asked for"; similar results
  under "More like this" with mode/score labels and prompt chips.
- **Relationship shelf**: detail and anchor search read local `behavior_v1`
  edges first; `hybrid_v1` fills cold starts and remaining slots. Theme clusters
  are display-only metadata and never create relationship edges.
- **Logs / Movie**: half-star logging, optional review with debounced vibe meter,
  watched/rated state, detail page.
- **Profile**: pfp, display name/bio, favorites, rating histogram, hearted films,
  genre affinity, sentiment distribution, engine transparency, Letterboxd import.

Rules: all API calls go through `src/hooks/api.js`; no raw fetch in components.

---

## 6. Data, Cohort, and Evaluation

- **Cohort**: 80 valid Letterboxd seed profiles, 102,522 watched rows, 85,178 rated
  rows, 47,323 likes harvested. Resolution left 9,136 resolved / 380 unresolved.
- **Imported**: 75,586 seed ratings, 37,465 likes, 10,309 watched-unrated rows.
- **Catalog**: 20,514 films, all embedded. The Letterboxd top-20,000 import
  resolved 19,803 rows; 196 were skipped as 404/TV and 1 remains unresolved.
- **Themes**: 100% coverage — 14,743 Letterboxd theme sets, 4,572 TMDB-keyword
  fallbacks, 1,164 genre fallbacks, 35 text fallbacks.
- **Assets**: 20,511/20,514 rows enriched. The three misses (`1778411`, `37452`,
  `1598414`) are legacy no-payload IDs (two TV/specials and one delisted short) with
  no current TMDB movie payload.
- **Graph**: behavior-first. 344,596 `behavior_v1` edges over 7,195 anchors
  (24 direct + 24 multi-hop expansion per anchor), plus 492,336 `hybrid_v1`
  fallback edges covering all 20,514 anchors; 836,932 `film_neighbors` rows
  total. Theme metadata remains 32 clusters / 36,563 `film_subniches` rows over
  14,743 films and is display-only. The 37 behavior communities / 6,735 films
  are internal diagnostics, not exposed subniches.
- **Audit**: 81 users (80 seeds + Mustafa), no duplicate catalog rows, SQLite
  `quick_check` OK. Mustafa: 623 ratings, 623 logged films, unchanged password hash.
- **Evaluation**: one deterministic user-level 20% holdout per persona (81 personas),
  warm and cold arms; held-out edges removed before CF and taste rebuild.
- **Current 20k metrics**: warm NDCG@20 **0.2308**, binary NDCG **0.2539**,
  Recall@20 **0.0820**, Recall@50 **0.1576**, MRR **0.4763**, coverage **2.19%**,
  intra-list similarity **0.2579**, cold NDCG **0.1095**.
- **CF guardrail**: baseline-residual item-item CF reached MAE **0.5445** vs target
  ≤0.5500. Historical 9,608-film v2 metrics remain in `reports/eval_v2.json`.
- **Latency**: warm feed ~1.25 s, search ~1.24 s, engine status ~3.9 s cold /
  cached afterward, local movie detail ~0.02 s.

---

## 7. Rules

1. Never hardcode the TMDB key; load from `.env` via python-dotenv.
2. Embeddings stay normalized; provider changes must bump the signature.
3. Ratings are 0.5–5.0 end-to-end (DB checks, Pydantic, UI stars).
4. Seeds are data rows only: no password, no login, no profile writes.
5. FastAPI dependency params need explicit `Depends(...)` defaults.
6. Literal routes (e.g. `/reviews/score`, `/feedback/impressions`) precede int paths.
7. The live DB is `backend/cinematch.db`; never confuse it with a stale root copy.
8. All API calls go through `src/hooks/api.js`.
9. No TypeScript, no CSS framework, no state-management library.
10. New backend behavior ships with tests; run `pytest -q` and `npm run build`
    before calling it done, and re-run `seed_audit.py --strict` after data changes.
