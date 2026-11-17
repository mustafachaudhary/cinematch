# CineMatch evaluation

- source: `/home/mustafa/Downloads/Odin/NLP/cinematch.ai/backend/cinematch.db`
- catalog: 20512 films
- personas: 81
- ranker: v2 (disabled: none)
- split: 20% per user, 20–200 rows

| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |
|---|---:|---:|---:|---:|---:|---:|---:|
| warm | 0.2096 | 0.2372 | 0.0696 | 0.1490 | 0.4468 | 0.5608 | 91.74% |
| cold | 0.1095 | 0.1311 | 0.0080 | 0.0176 | 0.2646 | 0.0000 | 0.00% |

- catalog coverage (warm): 2.58%
- mean popularity (warm): 15.06
- intra-list similarity (warm): 0.2633
- CF support share (warm): 98.02%
