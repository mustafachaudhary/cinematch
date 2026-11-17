# ranking v2 + subniche + theme lift-weighting

- source: `/home/mustafa/Downloads/Odin/NLP/cinematch.ai/backend/cinematch.db`
- catalog: 20514 films
- personas: 81
- ranker: v2 (disabled: none)
- split: 20% per user, 20–200 rows

| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |
|---|---:|---:|---:|---:|---:|---:|---:|
| warm | 0.2275 | 0.2546 | 0.0777 | 0.1537 | 0.4978 | 0.5445 | 91.58% |
| cold | 0.1095 | 0.1311 | 0.0080 | 0.0176 | 0.2646 | 0.0000 | 0.00% |

- catalog coverage (warm): 2.40%
- mean popularity (warm): 17.01
- intra-list similarity (warm): 0.2459
- CF support share (warm): 95.31%
