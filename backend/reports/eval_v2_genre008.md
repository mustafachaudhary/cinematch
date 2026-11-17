# CineMatch evaluation v2

- source: `/tmp/opencode/cinematch_seed_import/final_import.db`
- catalog: 9608 films
- personas: 81
- ranker: v2 (disabled: none)
- split: 20% per user, 20–200 rows

| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |
|---|---:|---:|---:|---:|---:|---:|---:|
| warm | 0.2266 | 0.2533 | 0.0765 | 0.1621 | 0.4278 | 0.5608 | 91.74% |
| cold | 0.1148 | 0.1363 | 0.0081 | 0.0180 | 0.2694 | 0.0000 | 0.00% |

- catalog coverage (warm): 5.29%
- mean popularity (warm): 15.72
- intra-list similarity (warm): 0.2533
- CF support share (warm): 99.07%
