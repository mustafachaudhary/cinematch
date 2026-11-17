# CineMatch evaluation targets

Authoritative baseline: `backend/reports/eval_baseline.json`  
Protocol: one deterministic 20% user-level holdout per persona (81 personas:
80 seed profiles + mustafa), held-out rows removed before item similarities
are rebuilt, warm and cold-start arms, graded and binary relevance.

## Baseline (ranking v1, 9,608-film catalog)

| metric | warm | cold |
|---|---:|---:|
| NDCG@20 graded | 0.1516 | 0.1148 |
| NDCG@20 binary | 0.1693 | 0.1363 |
| Recall@20 | 0.0516 | 0.0081 |
| Recall@50 | 0.1194 | 0.0180 |
| MRR | 0.2919 | 0.2694 |
| CF MAE | 0.5608 | — |
| CF coverage | 91.74% | — |
| catalog coverage | 5.00% | — |
| intra-list similarity | 0.3062 | — |

## v2 acceptance gates

These are the minimum gates for keeping ranking v2. All are measured on the
same frozen holdout and catalog.

| gate | target | rationale |
|---|---:|---|
| warm NDCG@20 graded | **≥ 0.1800** (+18.8% rel.) | primary relevance lift |
| warm Recall@20 | **≥ 0.0600** (+16.3% rel.) | not just reordering the same hits |
| warm MRR | **≥ 0.3300** (+13.1% rel.) | earlier first relevant result |
| CF MAE | **≤ 0.5500** | CF must not regress |
| catalog coverage (warm) | **≥ 5.00%** | no popularity collapse |
| intra-list similarity | **≤ 0.3100** | no homogeneity regression |
| cold NDCG@20 | **≥ 0.1100** | cold start must not regress |
| feed p95 latency | **≤ 2.5 s** | local interactive budget |
| search p95 latency | **≤ 2.0 s** | local interactive budget |

## Ablation rule

Every signal must earn its place. A signal is kept only if disabling it lowers
warm NDCG@20 by at least 0.5% relative, or if it materially improves a
secondary metric without hurting NDCG. Runs use:

```bash
CINEMATCH_DISABLE=liked,theme,genre,collection,cf \
  ../.venv/bin/python scripts/eval_recommender.py --db <copy> --out <report>
```

The final report must include the enabled weights and disabled signals.
