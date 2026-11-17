# ablation: no subniche

- source: `/home/mustafa/Downloads/Odin/NLP/cinematch.ai/backend/cinematch.db`
- catalog: 20514 films
- personas: 81
- ranker: v2 (disabled: subniche)
- split: 20% per user, 20–200 rows

| arm | NDCG@20 graded | NDCG@20 binary | Recall@20 | Recall@50 | MRR | CF MAE | CF cov. |
|---|---:|---:|---:|---:|---:|---:|---:|
| warm | 0.2308 | 0.2538 | 0.0820 | 0.1575 | 0.4763 | 0.5445 | 91.58% |
| cold | 0.1095 | 0.1311 | 0.0080 | 0.0176 | 0.2646 | 0.0000 | 0.00% |

- catalog coverage (warm): 2.19%
- mean popularity (warm): 17.27
- intra-list similarity (warm): 0.2581
- CF support share (warm): 96.85%
