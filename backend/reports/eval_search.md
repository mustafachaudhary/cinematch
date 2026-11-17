# Search evaluation

- catalog: 20514 films, personas: 81 (81 with taste modes)
- same 20% user-level holdout as the feed eval

## Mood suite (gates search-side taste modes)

| nudge | NDCG@20 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|
| global vector | 0.0041 | 0.0017 | 0.0036 | 0.0211 |
| max-over-modes | 0.0040 | 0.0015 | 0.0033 | 0.0219 |

Paired per-persona deltas (modes − global):

- ndcg20: -0.0000 (t=-0.20, n=81)
- recall10: -0.0002 (t=-2.26, n=81)
- recall20: -0.0003 (t=-1.60, n=81)
- mrr: +0.0008 (t=+0.66, n=81)

## Anchor suite (baseline, taste-independent)

- NDCG@20 0.0492 · Recall@10 0.0129 · Recall@20 0.0244 · MRR 0.1816 (648 anchor queries)
