# Taste modes — acceptance gate report

Date: 2026-11-10 · Catalog: 20,514 films · Personas: 81 (20% user-level holdout)
Gate rule (from `eval_targets.md`): a signal is kept only if disabling it lowers
warm NDCG@20 by ≥ 0.5% relative, or it materially improves a secondary metric
without hurting NDCG.

## What was built

`services/taste.py:build_taste_modes` clusters a user's positively-rated film
embeddings into 2–4 k-means centroids ("taste modes"). Ranking's content
component becomes `max(global taste cosine, best-mode cosine)` — a film only
needs to match ONE side of the user to score fully, so an eclectic viewer is
no longer collapsed into their mediocre middle. Modes are labeled from member
subniches (genre fallback) and surfaced in explanations ("Matches your High
speed and special ops side") and the `signals.mode` payload. Ablatable via
`CINEMATCH_DISABLE=modes`.

Design notes from calibration:
- Silhouette on real embedding clouds runs 0.03–0.06 (taste clusters overlap
  heavily), so it cannot separate one taste from many; it only rejects
  degenerate negative fits (`MODE_SILHOUETTE_MIN = 0.0`).
- The real gate is **centroid distinctness** (`MODE_DISTINCT_MAX = 0.90`):
  the halves of a single-taste continuum are near-identical directions and
  must not become modes. mustafa's two real modes sit at 0.79 centroid cosine.
- `MODE_MIN_FILMS = 12` positives, `MODE_MIN_CLUSTER = 4`, fixed seeds —
  fully deterministic.

## Results

| config | warm NDCG@20 graded | NDCG@20 binary | Recall@20 | MRR | coverage |
|---|---:|---:|---:|---:|---:|
| no modes (`ablation_no_modes`) | 0.2328 | — | 0.0794 | 0.4945 | 2.45% |
| **modes on (`eval_modes`)** | **0.2441** | — | **0.0853** | 0.4994 | 2.52% |

Paired per-persona deltas (n = 81):

| metric | mean delta | relative | paired t | wins / losses |
|---|---:|---:|---:|---:|
| NDCG@20 graded | +0.0113 | **+4.85%** | +1.49 | 41 / 38 |
| Recall@20 | +0.0058 | **+7.33%** | +1.68 | 35 / 19 |
| MRR | +0.0049 | +0.99% | +0.15 | 24 / 24 |

## Verdict: keep (strongest signal added since ranking v2)

- Disabling modes lowers warm NDCG@20 by 4.85% relative — nearly 10× the
  0.5% gate — and improves Recall@20 by 7.3% instead of trading it (the
  subniche signal traded −3% recall for ordering quality; modes give both).
- Cumulative on the 20k catalog: v2 baseline 0.2308 → +subniche 0.2328 →
  +modes **0.2441** (+5.8% over ranking v2, +60.9% over the v1 0.1516 baseline
  measured on the easier 9,608-film catalog).
- Live profile: mustafa (623 ratings, 408 positive) splits into
  "Underdogs and coming of age" (182 films) and "High speed and special ops"
  (226 films); 17/20 feed rows carry a mode attribution.

## Caveats

- Paired t = +1.49 at n = 81 (p ≈ 0.14) — below conventional significance;
  the same protocol needed bootstrap CIs to certify ranking v2's bigger jump.
  Treat +4.85% as strong evidence, not proof; re-check after the next cohort
  import (n ≈ 400 would make a delta this size p < 0.05).
- Modes share the label-lift caveat from `eval_subniche_gate.md`: the subniche
  labels used for naming come from the artifact measured on the full cohort;
  labels affect explanations only, never scores.
- Mode fitting adds k-means (k ≤ 4, n_init 4) per ranking call — ~10 ms at
  408 members, well inside the feed latency budget.

## Next experiments

1. Search-side modes: the general/mood search nudge still uses the global
   vector only (`search.py`); pass mode vectors for consistency.
2. Time-decay on rating weights (ratings never age; data already in
   `logged_movies.logged_at`).
3. Impression learning: shown-but-skipped evidence is collected
   (`FeedImpression`) but never read by the ranker.
