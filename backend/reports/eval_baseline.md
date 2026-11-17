# CineMatch evaluation baseline

- source: `/tmp/opencode/cinematch_seed_import/final_import.db`
- catalog: 9608 films
- personas: 81
- split: 20% per user, 20–200 rows

| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |
|---|---:|---:|---:|---:|---:|---:|---:|
| warm | 0.1516 | 0.1693 | 0.0516 | 0.1194 | 0.2919 | 0.5608 | 91.74% |
| cold | 0.1148 | 0.1363 | 0.0081 | 0.0180 | 0.2694 | 0.0000 | 0.00% |

- catalog coverage (warm): 5.00%
- mean popularity (warm): 15.56
- intra-list similarity (warm): 0.3062
- CF support share (warm): 97.10%
