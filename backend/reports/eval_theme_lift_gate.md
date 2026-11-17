# Theme lift-weighting — acceptance gate report

Date: 2026-11-09 · Catalog: 20,514 films · Personas: 81 (20% user-level holdout)
Question: does applying cohort lift weighting to the 207 *theme* labels (the
mechanism that rescued the subniche signal) improve ranking over the current
best config (subniche lift-weighted, w_subniche=0.10, w_theme=0.12)?

## Result

| config | warm NDCG@20 graded | Recall@20 | MRR | coverage |
|---|---:|---:|---:|---:|
| current best (subniche lift-weighted, `eval_subniche`) | 0.2328 | 0.0794 | 0.4945 | 2.45% |
| + theme lift-weighting (`eval_themelift`) | 0.2275 | 0.0777 | 0.4978 | 2.40% |

Paired per-persona deltas (n = 81):

| metric | mean delta | relative | paired t | wins / losses |
|---|---:|---:|---:|---:|
| NDCG@20 graded | −0.0053 | −2.28% | **−2.16** | 23 / 33 |
| Recall@20 | −0.0018 | −2.22% | −2.14 | — |
| MRR | +0.0033 | +0.66% | +0.79 | — |

## Verdict: rejected, mechanism kept behind an opt-in flag

The NDCG@20 regression is the first statistically significant delta measured
on this protocol (|t| > 2). Theme lift-weighting is reverted to OFF by
default; `CINEMATCH_THEME_LIFT=1` re-enables it for future experiments. The
subniche lift-weighting (32 coarse clusters, stable estimates) is unaffected.

## Why it failed where subniches succeeded

1. **Support asymmetry.** Subniche clusters hold 1,900–12,000 interactions
   each — lift estimates are stable. Measured theme lifts rest on n ≈ 30–100
   interactions; their 1.5×+ multipliers amplify noise into the ranker.
2. **Trust is already handled per user.** Theme affinity is support-gated
   (THEME_GATE = 5 rated films) and normalized per user; cohort-level
   reweighting double-counts evidence the per-user gate already filters.
3. **Cohort lift ≠ conditional value.** A below-base label like `sequel`
   still carries real per-user signal once *this user* has rated it 5 times.
   The multiplier overrode the individual's own evidence with the crowd's.

Rule of thumb extracted: cohort-lift weighting is safe at coarse granularity
with strong support, harmful at fine granularity. Revisit only when a theme's
support clears ~500 interactions.
