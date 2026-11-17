# CineMatch ranking v2 final (full themes, genre weight 0)

- source: `/home/mustafa/Downloads/Odin/NLP/cinematch.ai/backend/cinematch.db`
- catalog: 9608 films
- personas: 81
- ranker: v2 (disabled: none)
- split: 20% per user, 20–200 rows

| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |
|---|---:|---:|---:|---:|---:|---:|---:|
| warm | 0.2453 | 0.2757 | 0.0849 | 0.1638 | 0.4313 | 0.5608 | 91.74% |
| cold | 0.1148 | 0.1363 | 0.0081 | 0.0180 | 0.2694 | 0.0000 | 0.00% |

- catalog coverage (warm): 5.05%
- mean popularity (warm): 16.82
- intra-list similarity (warm): 0.2604
- CF support share (warm): 99.38%
