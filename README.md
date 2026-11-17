# CineMatch

A personalized movie recommendation app built around a measurable taste engine:
sentence-transformer embeddings, a multi-mode taste profile, item-item collaborative
filtering, data-derived subniches, VADER review sentiment, and hybrid MMR-diversified
ranking — plus a Letterboxd import that wires your history straight into all of it.

Every weight in the ranker earned its place through an offline evaluation, and every
card in the feed carries a reason it can show you.

## What's inside

| Layer | What actually runs |
|---|---|
| Embeddings | `all-MiniLM-L6-v2` (384-d, normalized) via sentence-transformers; automatic TF-IDF+SVD (LSA) fallback when the model isn't available |
| Taste profile | Midpoint-centered taste vector (3.0 = neutral; disliked films pull at 0.35 weight; review sentiment modulates each film by `1 + 0.35·compound`) split into **taste modes** — 2–4 k-means centroids over your positive history, gated so a single-taste viewer gets none. An item only needs to match one side of you to score fully |
| Collaborative filtering | Item-item cosine on baseline-residual ratings, shrunk by co-rating support (`sim · n/(n+8)`), top-20 neighbors, confidence-scaled blending, fingerprint-invalidated caches |
| Subniche affinity | 32 NMF-derived subniches over Letterboxd theme co-occurrence; your affinity per subniche is support-gated and weighted by measured cohort lift, so broadly-watched-but-under-loved niches push less |
| Ranking v2 | Content taste (mode-aware) 0.48, themes 0.12, subniches 0.10, collection 0.04, quality prior 0.16, CF max 0.35 confidence-scaled; MMR (λ=0.70) for diversity. Genre and direct liked-centroid weights are 0 after ablations hurt |
| Seed cohort | 80 pseudonymous Letterboxd profiles, 75,586 ratings and 37,465 likes, used as recommendation data only — seeds cannot log in |
| Search | Three modes: **anchor** ("something like Interstellar"), **mood** ("I want to cry"), and general semantic+lexical blend; exact title matches come first with "More like this" below. Anchor search reads the local behavior graph first (co-raters, co-likers, franchise evidence) |
| Feedback | Explicit "Not interested" suppression plus render-confirmed feed impressions |

Everything is measurable: `GET /movies/engine-status` reports catalog size, embedding
backend, CF state (raters/ratings/similarity pairs), and the sentiment model in use.
The offline evaluators and every gate report live in `backend/reports/`.

## Tech stack

- **Backend:** FastAPI, SQLAlchemy 2, SQLite, sentence-transformers, scikit-learn, vaderSentiment, pytest
- **Frontend:** React 18, Vite, vanilla CSS design system (dark cinematic theme, no UI library)
- **Data:** TMDB API

## Quick start

Prerequisites: Python 3.10+, Node 18+, a free [TMDB API key](https://www.themoviedb.org/settings/api).

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Configure `backend/.env`:

```
TMDB_API_KEY=your_key_here
DATABASE_URL=sqlite:///./cinematch.db
```

Seed the catalog (first time only; the live catalog has 20,514 films — the Letterboxd
top 20,000 plus the original catalog). `seed.py` can build a smaller ~1,200-film
development catalog across 19 genres:

```bash
python seed.py --pages 3     # ~5 min; downloads the MiniLM model (~90 MB)
```

Run it:

```bash
uvicorn main:app --reload    # http://localhost:8000 — /docs for OpenAPI
```

On startup the app creates/migrates the schema, warms up embeddings, and imports any
Letterboxd export found in `data/`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173
```

Set `VITE_API_URL` to point the frontend at a non-default backend.

## Using it

- **Discover** — ranked picks with a match % and a specific reason ("Squarely in your
  Gritty crime and ruthless gangsters niche", "Matches your Underdogs and coming of
  age side"). Cold-starts on quality×popularity until you rate something.
- **Search** — natural language across three modes; the response tells you which mode
  fired and what anchored it.
- **My Films** — log anything ever made (TMDB lookup), half-star ratings on a 0.5–5
  scale, optional review with a live sentiment "vibe meter".
- **Watchlist** — films set aside for later, kept out of the feed until you watch them.
- **Profile** — favorites, rating histogram, hearted films (4.5★+), genre affinities,
  sentiment distribution, engine transparency panel, and Letterboxd zip import.
- **Accounts** — a real auth gate: first visit lands on the sign-in screen; create an
  account or log back in and your session persists locally. Passwords follow NIST
  guidance (8–128 chars, length over composition rules) and are stored as salted
  PBKDF2-SHA256; login failures are generic (no user enumeration), rate-limited per
  account, and every auth event is logged. The 80 harvested Letterboxd profiles are
  flagged seed rows with no password: they can never log in, and the API rejects them
  with 401/403 even if a username is guessed.
- **Your reviews** — reviews you write while logging are collected on the Profile
  page with their VADER vibe label.

### Letterboxd import

Export your data from Letterboxd (Settings → Import & Export), then upload the zip on
the Profile page. `ratings.csv` entries are resolved against TMDB (year-matched),
embedded, and written into both your logs and the CF rating matrix — with the export's
`Date` column preserved, so rating history keeps its timeline. `watched.csv` marks
films as seen so they stop appearing in your feed.

## How recommendations work

1. Every catalog film is embedded from `{title}. {overview} Genres: …` into a
   unit-normalized 384-d vector.
2. Your **taste vector** is a midpoint-centered weighted average of what you rated.
   Your positive history is additionally clustered into 2–4 **taste modes** when it
   genuinely splits (the gates: ≥12 positive ratings, ≥4 films per cluster, cluster
   centroids must point in directions less than 0.90 cosine apart). Content score is
   the best match across the global vector and the modes — a Kurosawa film is not
   diluted by this week's comedy phase.
3. **Subniche affinity** scores each candidate against your per-subniche affinities
   from rated films and likes, gated on 3 subniche-bearing ratings. Each subniche is
   weighted by its measured cohort lift (`reports/label_lift.json`): comedy niches
   this cohort rates below base rate push at 0.81, crime niches at 1.10. Measured on
   85k cohort interactions by `scripts/measure_label_lift.py`; a missing artifact
   falls back to unweighted affinity.
4. Ranking blends bounded, support-gated signals. Missing signals are removed and the
   remaining weights renormalized. Two weights are intentionally zero, each with the
   ablation that zeroed it recorded in `services/ranker_config.py`: genre duplicated
   theme metadata once themes hit full coverage, and the direct liked-centroid hurt
   NDCG.
5. Once ≥2 profiles have ratings, item-item CF predictions join the blend, scaled by
   per-prediction confidence and capped at weight 0.35.
6. MMR (λ=0.70) diversifies the final list; "Not interested" feedback removes films
   from that user's candidate pool, and rendered impressions are recorded for future
   evaluation.
7. Reviews are scored by VADER; aggregate sentiment shapes both your taste vector and
   the profile page. Optional time-decay (`CINEMATCH_DECAY=1`) down-weights ratings
   older than a year (half-life 365 days, floor 0.35) — off by default because the
   seed cohort carries no per-rating dates yet; ZIP imports preserve real dates.

Ratings use a 0.5–5.0 star scale everywhere (Letterboxd-compatible).

### Measured results

On the frozen 81-persona, one-holdout-per-user evaluation (`backend/reports/`):

| metric | v1 baseline | ranking v2 |
|---|---:|---:|
| warm NDCG@20 | 0.1516 | **0.2453** (+61.8%) |
| warm Recall@20 | 0.0516 | **0.0849** (+64.6%) |
| warm MRR | 0.2919 | **0.4313** (+47.8%) |
| cold NDCG@20 | 0.1148 | 0.1148 |
| CF MAE | 0.5608 | 0.5608 |

That table is the historical 9,608-film v2 evaluation. On the current 20,514-film
catalog, each accepted signal stacks on the same frozen holdout:

| config | warm NDCG@20 | Recall@20 | MRR | evidence |
|---|---:|---:|---:|---|
| ranking v2 baseline | 0.2308 | 0.0820 | 0.4763 | `eval_final_20k.json` |
| + subniche (lift-weighted) | 0.2328 | 0.0794 | 0.4945 | `eval_subniche_gate.md` |
| + taste modes | **0.2441** | **0.0853** | 0.4994 | `eval_modes_gate.md` |

Not every experiment survived the gate, which is the point: theme-level lift weighting
regressed NDCG@20 by 2.3% (t = −2.16) and was reverted with the rule written down
(`eval_theme_lift_gate.md`); search-side modes showed no measurable effect and were
reverted (`eval_search_gate.md`). The expanded catalog carries 14,743 Letterboxd theme
sets, 36,563 theme-subniche assignments, and 344,596 `behavior_v1` relationship edges
built from local ratings/likes/watches, with 492,336 `hybrid_v1` edges as the
cold-start fallback. Movie detail and anchor search read behavior edges first. Theme
clusters are display metadata only. Local smoke checks put a warm feed around 1.25 s,
search around 1.24 s, and a cache-hit movie detail around 0.02 s.

## API overview

```
GET  /movies/feed?limit=18&exclude=1,2 personalized or cold-start feed (+ reasons);
                                       exclude lets Refresh picks return new films
GET  /movies/search?q=...             mode-aware search; exact title first, then similar
GET  /movies/tmdb-search?q=...        TMDB title lookup for logging
GET  /movies/engine-status            transparency: embeddings, CF state, models
GET  /movies/profile-summary          taste summary, genre affinity, sentiment stats
GET  /people/{person_id}              actor/director filmography from catalog credits
GET  /feedback                        your hidden / not-interested films
POST /feedback/{tmdb_id}              hide or mark not interested
DEL  /feedback/{tmdb_id}              undo feedback
POST /feedback/impressions            record rendered feed impressions
GET  /watchlist/                      your set-aside films
POST /watchlist/{tmdb_id}             add to watchlist
DEL  /watchlist/{tmdb_id}             remove from watchlist
GET  /logs/                           your rated films
POST /logs/ {tmdb_id, rating}         log + rate (0.5–5.0)
DEL  /logs/{tmdb_id}
POST /reviews/{tmdb_id}               save review (auto VADER-scored)
POST /reviews/score                   stateless live scoring (vibe meter)
GET  /reviews/                        your reviews + aggregate
GET  /profile/  PUT /profile/         profile read/update (favorites, bio, name)
POST /profile/signup | login | signout
POST /profile/upload-pfp              avatar upload (validated)
POST /profile/import-letterboxd       zip import (ratings.csv + watched.csv)
```

All user-scoped routes take identity from the `X-Username` header.

## Development

```bash
cd backend && python -m pytest -q                 # 82 backend tests
cd backend && python scripts/seed_audit.py --strict
cd frontend && npm run build                      # production build check
```

Offline evaluation and latency benchmark (source DB is copied; never mutated):

```bash
cd backend
python scripts/eval_recommender.py --db cinematch.db --out reports/eval_v2.json
python scripts/eval_search.py --db cinematch.db --out reports/eval_search.json
python scripts/measure_label_lift.py --db cinematch.db --out reports/label_lift.json
python scripts/bench.py --user mustafa --n 12 --warmup 2 --out reports/bench_v2.json

# Rebuild the local behavior graph and evaluate its holdout retrieval.
python scripts/build_behavior_graph.py --db cinematch.db --allow-live \
  --report reports/behavior_graph_full_live.json
python scripts/eval_behavior_graph.py --db cinematch.db --limit 0 \
  --report reports/eval_behavior_graph_full.json
```

Maintenance scripts (run from `backend/`):

```bash
python scripts/import_letterboxd_profiles.py <user1> [user2 ...] [--per-user 120]
python scripts/import_seed_profiles.py ...        # cohort import from resolved rows
python scripts/apply_themes.py                    # Letterboxd + TMDB keyword + fallback themes
python scripts/build_film_graph.py                # NMF subniches + hybrid neighbor edges
python scripts/build_behavior_graph.py --allow-live   # behavior-first relationship edges
python scripts/enrich_tmdb_catalog.py             # paced TMDB metadata enrichment
python scripts/seed_audit.py --strict              # corruption/coverage audit
python scripts/retire_old_seeds.py                # only after audit passes
```

## Notes

- SQLite lives at `backend/cinematch.db`; startup migrations rebuild legacy
  single-user tables in place (your ratings survive). The live catalog is the
  clean 80-profile cohort plus the real `mustafa` account.
- Seed accounts are data rows, not login accounts. Never give a seed row a password;
  `/profile/login` and the feed dependency both reject them.
- Every accepted signal has a gate report naming the run that accepted it; every
  rejected experiment has one naming the number that killed it. Start with
  `reports/eval_modes_gate.md` and `reports/eval_targets.md`.
- Never commit `.env`. The seed script only embeds films it hasn't embedded yet.
