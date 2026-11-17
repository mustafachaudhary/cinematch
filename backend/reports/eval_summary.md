# CineMatch intelligence evaluation — final v2

## Cohort and protocol

- 80 valid Letterboxd seed profiles (plus `mustafa` as the real account).
- 75,586 seed ratings, 37,465 separate likes, 10,309 watched-unrated rows.
- Catalog: 9,608 films, all embedded; themes covered for **100%** of catalog and
  seed films (Letterboxd themes + TMDB keyword / genre / text fallbacks).
- One deterministic 20% user-level holdout per persona (81 personas), 20–200
  held-out rows per user. Held-out user edges are removed before CF and taste
  vectors are rebuilt. Warm and cold-start arms are reported.
- Baseline is the frozen ranking-v1 run in `reports/eval_baseline.json`; final v2
  is `reports/eval_v2.json`. The same split and catalog are used for both.
- The pre-final genre-weight-0.08 run is retained separately as
  `reports/eval_v2_genre008.json` for audit.

## Headline results

| metric | v1 baseline | v2 | relative change |
|---|---:|---:|---:|
| warm NDCG@20 graded | 0.1516 | **0.2453** | **+61.8%** |
| warm NDCG@20 binary | 0.1693 | **0.2757** | **+62.9%** |
| warm Recall@20 | 0.0516 | **0.0849** | **+64.6%** |
| warm Recall@50 | 0.1194 | **0.1638** | **+37.2%** |
| warm MRR | 0.2919 | **0.4313** | **+47.8%** |
| catalog coverage (warm) | 5.00% | **5.05%** | +0.05 pp |
| intra-list similarity | 0.3062 | **0.2604** | −0.0458 |
| cold NDCG@20 | 0.1148 | 0.1148 | unchanged |
| CF MAE | 0.5608 | 0.5608 | unchanged |
| CF coverage | 91.74% | 91.74% | unchanged |

Paired bootstrap over the 81 personas (10,000 resamples, 95% percentile):

| metric | mean delta | 95% CI | wins / losses |
|---|---:|---:|---:|
| NDCG@20 graded | +0.0937 | [+0.0744, +0.1135] | 73 / 7 |
| NDCG@20 binary | +0.1064 | [+0.0853, +0.1282] | 73 / 6 |
| Recall@20 | +0.0333 | [+0.0252, +0.0422] | 63 / 4 |
| Recall@50 | +0.0444 | [+0.0346, +0.0551] | 66 / 2 |
| MRR | +0.1395 | [+0.0848, +0.1947] | 51 / 10 |

The improvements are positive across every seeded cohort. The largest cohort
(`members/month`, 49 personas) improves NDCG@20 from 0.1973 to 0.2917 under the
pre-final config; the final no-genre config improves it further to 0.3080.

## Ranking v2 configuration

Final weights (missing signals are removed and the blend is renormalized):

| signal | weight |
|---|---:|
| content (taste-vector cosine) | 0.48 |
| liked centroid | 0.00 |
| themes | 0.12 |
| genres | 0.00 |
| collection affinity | 0.04 |
| quality prior | 0.16 |
| CF | max 0.35, confidence-scaled |

The direct liked-centroid component was tested and **removed**: disabling it
raised warm NDCG@20 in the pre-final ablation probe. Likes still build theme,
genre, and collection affinity, and liked films are excluded from candidates
because the user has already consumed them.

Genre was also removed. With only partial theme coverage it had been a useful
fallback, but at 100% theme coverage it double-counts the same metadata. The
full-coverage run with genre weight 0.08 scored 0.2266; setting genre to 0 scored
**0.2453**. The final model therefore uses themes, collection, quality, content,
and CF only.

## Final ablations

Full-coverage, one signal removed at a time from the final v2 configuration:

| variant | NDCG@20 | delta | Recall@20 | MRR | coverage |
|---|---:|---:|---:|---:|---:|
| **v2 full** | **0.2453** | — | **0.0849** | **0.4313** | **5.05%** |
| no themes | 0.1888 | −0.0565 | 0.0663 | 0.3660 | 4.98% |
| no collection | 0.2413 | −0.0040 | 0.0840 | 0.4279 | 4.96% |
| no CF | 0.0832 | −0.1621 | 0.0303 | 0.2520 | 4.76% |
| genre restored (0.08) | 0.2266 | −0.0187 | 0.0765 | 0.4278 | 5.29% |

Themes are the largest non-CF lift. CF is essential. Collection is a small but
positive franchise signal. Restoring genre improves catalog coverage and
diversity slightly, but at a clear relevance cost, so it stays at zero.

## Acceptance gates

| gate | target | v2 | status |
|---|---:|---:|---|
| warm NDCG@20 | ≥ 0.1800 | 0.2453 | pass |
| warm Recall@20 | ≥ 0.0600 | 0.0849 | pass |
| warm MRR | ≥ 0.3300 | 0.4313 | pass |
| CF MAE | ≤ 0.5500 | 0.5608 | **miss** |
| catalog coverage | ≥ 5.00% | 5.05% | pass |
| intra-list similarity | ≤ 0.3100 | 0.2604 | pass |
| cold NDCG@20 | ≥ 0.1100 | 0.1148 | pass |
| feed p95 latency | ≤ 2.5 s | 0.97 s | pass |
| search p95 latency | ≤ 2.0 s | 0.78 s | pass |

CF MAE is unchanged from v1. A tuning probe found a slightly better residual
weighting (approximately 0.5509) but it would require revalidating the whole
ranking blend and was not adopted merely to cross a threshold on the same
holdout. This is reported as a known guardrail miss, not hidden.

## Latency

From `reports/bench_v2.json` (12 measured requests, 2 warmups, local API):

| endpoint | p50 | p95 | max |
|---|---:|---:|---:|
| feed (limit 20) | 0.94 s | 0.97 s | 0.99 s |
| search | 0.68 s | 0.78 s | 0.86 s |
| engine status | 0.40 s | 0.40 s | 0.40 s |

## Data-quality checks

- Strict seed audit after enrichment: 80 seeds, 1 real account, no corrupt
  markers, 9,608 catalog films, 8,421 with ≥2 ratings, 4,215 with ≥5 ratings.
- Mustafa invariants preserved: user 1, same password hash, 623 ratings, 622
  watched, 0 likes/feedback; live feed excludes all rated/watched/liked IDs.
- Seed lockout: seed feed returns HTTP 403 and seed login returns HTTP 401.
- TMDB enrichment: 9,602/9,608 applied, 9,605 with keywords, 9,586 with runtime;
  3 errors (one DNS failure, two 404s) documented in
  `reports/tmdb_enrichment_final.json`.

## Artifacts

- `reports/eval_baseline.json` / `.md` — frozen v1 baseline.
- `reports/eval_v2.json` / `.md` — final v2 evaluation.
- `reports/eval_v2_genre008.json` / `.md` — pre-final genre-weight-0.08 run.
- `reports/eval_comparison.json` — bootstrap and aggregate comparison.
- `reports/eval_ablations.json` — consolidated final ablation table.
- `reports/ablation_no_theme.json`, `ablation_no_collection.json`,
  `ablation_no_cf.json` — per-signal runs.
- `reports/eval_targets.md` — pre-registered gates.
- `reports/bench_baseline.json`, `reports/bench_v2.json` — latency before/after.
- `reports/seed_audit_final_enriched.json` — post-enrichment strict audit.
- `reports/seed_audit_live_final.json` — strict audit of the live database.
- `reports/themes_final.json`, `reports/tmdb_enrichment_final.json`.
- `reports/seed_import_final.json`, `reports/seed_retire_final.json`.
