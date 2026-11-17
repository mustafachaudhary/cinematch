# Search evaluation — first gate, and what it found

Date: 2026-11-10 · Catalog: 20,514 films · Personas: 81 (same deterministic
20% user-level holdout as the feed eval, `scripts/eval_search.py`)

Search never had an eval. This report establishes the baseline and gates the
first candidate change (search-side taste modes). Two suites:

* **Mood suite** (gates personalization): the 8 canonical mood queries,
  relevance = held-out films the persona rated above their threshold within
  the mood's genre pool, ranked within that pool.
* **Anchor suite** (baseline): held-out highly-rated films as "movies like X"
  anchors; relevance = the persona's other held-out highly-rated films.

## Results

| suite | NDCG@20 | Recall@10 | Recall@20 | MRR |
|---|---:|---:|---:|---:|
| mood — global nudge | 0.0041 | 0.0017 | 0.0036 | 0.0211 |
| mood — modes nudge | 0.0040 | 0.0015 | 0.0033 | 0.0219 |
| anchor (taste-independent) | 0.0492 | 0.0129 | — | — |

Paired per-persona delta (modes − global): ≈ 0 on every metric.

## Verdict: search-side modes rejected; instrument kept

The modes nudge makes no measurable difference (Δ ≈ −0.0001, both arms
statistically identical). Per the ablation culture, the router no longer
builds modes for search — it cost a per-request k-means fit for nothing.
The `taste_modes` hook in `search.search` stays (dormant, tested) for a
future change that earns it.

## What the eval actually revealed

1. **Mood search is effectively unpersonalized.** Below-random recall of the
   persona's own loved films within the browsable pool (0.0036 recall@20 vs
   ~0.008 expected from a random top-20): the mood/semantic/quality terms
   (85% of the score) are persona-independent, and the taste nudge at 0.15
   weight cannot reorder anything. If search personalization ever matters,
   the nudge *weight* itself must go through this gate first.
2. **Protocol design notes** (three iterations, all reproducible):
   - v1 had a dropped `_gain` zero-guard → negative NDCG; fixed to match
     `eval_recommender`.
   - v2 ranked within the full 20k catalog → no statistical power (a 0.15
     nudge cannot lift a film 1000+ positions).
   - v3 ranks within the mood's genre pool — the protocol that produced
     these numbers and the only one with the power to detect a real nudge.
3. **Anchor baseline** (first measurement ever): anchor retrieval hits the
   persona's other held-out loved films in the top-10 only 1.3% of the time.
   Note the relevance definition is strict — "films YOU will love given you
   love X" — harder than the product promise ("films related to X"); treat
   1.3% as the floor to improve, not a bug.

## Time-decay: shipped opt-in, uncertifiable on this cohort

The seed cohort carries no per-rating dates (all stamped on import day;
`user_likes.liked_at` is NULL everywhere), and a uniform date column makes
decay an exact no-op — every weight scales by the same constant, cancelling
in all normalizations. The mechanism (`taste.age_multiplier`, half-life 365d,
floor 0.35) ships **off by default** (`CINEMATCH_DECAY=1` to enable) with
tests; it activates the moment real dates exist. The ZIP importer now
captures Letterboxd's `Date` column into `logged_movie.logged_at` /
`rating.rated_at`, so future imports carry the temporal signal the seed
harvester cannot provide. Gate run deliberately deferred until dates exist.

## Next experiments

1. Impression learning (shown-but-skipped evidence; needs live-app usage or a
   simulated-impression extension to the eval protocol).
2. Raise the search taste-nudge weight through the mood gate now that the
   protocol can detect it.
3. Calibration of 0–100 match scores against the holdout.
