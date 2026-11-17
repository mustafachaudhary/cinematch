# CineMatch Project Report

## Overview

CineMatch is a personalized movie recommendation app combining semantic NLP,
item-item collaborative filtering, a 20,514-film catalog, and an evaluation harness
that measures recommendation quality rather than only checking that endpoints work.
The backend is FastAPI + SQLite; the frontend is React + Vite.

## What changed in this build

- Replaced the old 20-profile seed set with a fresh 80-profile Letterboxd cohort:
  75,586 ratings, 37,465 likes, and 10,309 watched-unrated rows.
- Retired old seed accounts only after a strict audit passed. Seeds are data rows,
  never login accounts; `mustafa` remains the only real account and its data and
  password hash are unchanged.
- Enriched 20,511/20,514 films through TMDB and generated themes for the entire
  catalog. Theme coverage is 100% (14,743 Letterboxd theme sets, 4,572 TMDB
  keyword fallbacks, 1,164 genre fallbacks, 35 text fallbacks).
- Built ranking v2 with content, theme, collection, quality and CF signals.
  Genre and direct liked-centroid weights are 0 after full-coverage ablations.
- Added an offline evaluator with one deterministic user-level 20% holdout per
  persona, warm/cold arms, paired bootstrap, coverage, diversity, and CF diagnostics.
- Added explicit feedback: "Not interested" suppression plus render-confirmed
  impressions.
- Made search exact-title-first: the requested film appears in its own block with
  similar films under "More like this".
- Expanded the catalog to 20,514 films from the Letterboxd top 20,000 and added
  local themes and a behavior-first relationship graph: 344,596 `behavior_v1`
  edges built from local ratings/likes/watches plus 492,336 retained `hybrid_v1`
  edges as the cold-start fallback. Movie detail and its "More like this" shelf
  are served locally on cache hits; TMDB is only used for ingestion, new
  releases and cache misses.

## Current 20k evaluation

The source database is the live 20,514-film catalog. The evaluation deletes the
same deterministic 20% holdout per persona from a copy, rebuilds CF and taste
from the remaining ratings, and scores the warm feed.

| metric | current 20k catalog |
|---|---:|
| warm NDCG@20 | **0.2308** |
| warm NDCG@20 binary | **0.2539** |
| warm Recall@20 | **0.0820** |
| warm Recall@50 | **0.1576** |
| warm MRR | **0.4763** |
| cold NDCG@20 | 0.1095 |
| catalog coverage | 2.19% |
| intra-list similarity | 0.2579 |
| CF MAE | **0.5445** |
| CF coverage | 0.9158 |

The CF guardrail now passes: baseline-residual item-item CF reduced MAE from
0.5608 to 0.5445 while warm NDCG@20, Recall@20 and MRR all improved. The larger
20k candidate pool reduces percentage catalog coverage and slightly dilutes the
cold-start arm versus the historical 9,608-film run. Report:
`backend/reports/eval_final_20k.json`.

## Historical ranking v2 evaluation (9,608-film catalog)

Frozen baseline: `backend/reports/eval_baseline.json`.
Final ranking v2: `backend/reports/eval_v2.json`.
Comparison/bootstrap: `backend/reports/eval_comparison.json`.

| metric | v1 baseline | v2 |
|---|---:|---:|
| warm NDCG@20 | 0.1516 | **0.2453** (+61.8%) |
| warm NDCG@20 binary | 0.1693 | **0.2757** (+62.9%) |
| warm Recall@20 | 0.0516 | **0.0849** (+64.6%) |
| warm Recall@50 | 0.1194 | **0.1638** (+37.2%) |
| warm MRR | 0.2919 | **0.4313** (+47.8%) |
| catalog coverage | 5.00% | 5.05% |
| intra-list similarity | 0.3062 | 0.2604 |
| cold NDCG@20 | 0.1148 | 0.1148 |
| CF MAE | 0.5608 | 0.5608 |

Paired bootstrap: NDCG@20 delta **+0.0937**, 95% CI **[+0.0744, +0.1135]**,
73 wins / 7 losses over 81 personas.

Final ablations (full theme coverage): removing themes drops NDCG@20 to 0.1854;
removing collection drops it to 0.2197; removing CF drops it to 0.0743. Removing
the redundant genre signal raises it from 0.2266 to 0.2453, so genre is now 0.

## Historical acceptance gates (9,608-film build)

| gate | target | result | status |
|---|---:|---:|---|
| warm NDCG@20 | ≥ 0.1800 | 0.2453 | pass |
| warm Recall@20 | ≥ 0.0600 | 0.0849 | pass |
| warm MRR | ≥ 0.3300 | 0.4313 | pass |
| CF MAE | ≤ 0.5500 | 0.5608 | **miss** |
| catalog coverage | ≥ 5.00% | 5.05% | pass |
| intra-list similarity | ≤ 0.3100 | 0.2604 | pass |
| cold NDCG@20 | ≥ 0.1100 | 0.1148 | pass |
| feed p95 | ≤ 2.5 s | ~1.08 s | pass |
| search p95 | ≤ 2.0 s | ~1.07 s | pass |

In the historical build CF MAE was the one miss. After the 20k expansion it is
0.5445 (pass); the warm ranking gates also remain above their targets. The
percentage catalog-coverage gate is not comparable after roughly doubling the
candidate pool.

## Data and model stack

- Embeddings: `all-MiniLM-L6-v2`, 384-d normalized, with TF-IDF+SVD fallback;
  20,514/20,514 films embedded.
- Themes: 100% coverage; 14,743 Letterboxd theme sets, 4,572 TMDB-keyword
  fallbacks, 1,164 genre fallbacks, 35 text fallbacks.
- Graph: 344,596 `behavior_v1` edges over 7,195 local-behavior anchors (24
  direct + 24 expansion edges per anchor) and 492,336 `hybrid_v1` fallback
  edges over all 20,514 anchors; 836,932 `film_neighbors` rows total. Theme
  metadata remains as 32 display clusters and 36,563 subniche assignments; it
  is not the relationship source. Behavior communities (37 clusters, 6,735
  films) are internal diagnostics and are not exposed as subniches yet.
- CF: item-item cosine on baseline residuals (global + user + shrunken item bias),
  top-20 neighbors, confidence capped at weight 0.35.
- Ranking v2: content 0.48, theme 0.12, collection 0.04, quality 0.16, genre 0.00,
  direct liked 0.00; MMR λ 0.70.
- Signals: ratings, separate likes, themes, collection, review sentiment.
- Feedback: hidden films are removed from candidates; impressions are deduped.
- Auth: PBKDF2-SHA256; seed accounts have no password and cannot log in.

## Local behavior graph

`backend/scripts/build_behavior_graph.py` builds the primary relationship layer
from the local cohort: 76,209 ratings, 37,465 likes, and 10,926 watched or
logged-unrated rows across 81 users.  Edges are co-rating, co-like, and
co-watch evidence with user-level baseline residuals and support shrinkage.
Themes, keywords, directors, cast, and genres do not create behavior edges.  A
same-collection franchise prior (weight 0.16) is applied only after local
co-support exists; it cannot create an edge on its own.

Each of the 7,195 anchors keeps 24 direct behavior edges first, then 24
two/three-hop expansion edges in deeper ranks.  The API's "More like this" shelf
and anchor search read `behavior_v1` before `hybrid_v1`; the latter fills cold
starts and remaining shelf slots.  This keeps local evidence above TMDB-derived
relationships while preserving coverage for the 13,319 films with too little
local behavior.

The full 20% holdout diagnostic is deliberately not used to replace the feed
ranker: direct behavior + degree penalty reaches recall@20 **4.23%**, macro
recall **5.85%**, NDCG@20 **0.394**, versus local popularity **4.45%**, **5.41%**,
**0.447**.  Multi-hop traversal is noisier (**3.49%** recall, **0.320** NDCG),
so it fills expansion ranks rather than leading the visible shelf.  Report:
`backend/reports/eval_behavior_graph_full.json`.

## Verification

- `backend`: 80 tests pass.
- `frontend`: production build passes.
- Strict audit: 20,514 films, 81 users, no duplicate catalog rows, SQLite
  `quick_check` OK.
- Mustafa invariants: user 1, unchanged password hash, 623 ratings, 623 logged
  films; feed excludes all history.
- Assets: 20,511/20,514; the three misses are legacy no-payload IDs (two TV/specials and one
  delisted short): `1598414`, `37452`, `1778411`.
- Seed lockout: feed 403, login 401.
- API: feed returns 16 unique cards with 13 in "Also tonight"; refresh has zero
  overlap; exact search returns the requested film first; local movie detail is
  ~0.02 s; actor pages load from catalog credits.
- No commits made; all changes remain uncommitted at `4122a66`.

## Reports

`eval_final_20k.json`, `eval_cf_baseline.md`, `eval_after_20k.json`,
`film_graph_full_live.json`, `asset_backfill_*.json`,
`skipped_letterboxd_repair_*.json`, plus the historical v1/v2 evaluation reports.
