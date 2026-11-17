"""Review sentiment analysis.

Uses VADER (Hutto & Gilbert, 2014) — a validated, rule-based sentiment model
tuned for short informal text, which is exactly what movie reviews are.
Compound score range: [-1, 1] (most negative → most positive).
"""

import functools

_VADER = None


def _analyzer():
    global _VADER
    if _VADER is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        _VADER = SentimentIntensityAnalyzer()
    return _VADER


@functools.lru_cache(maxsize=2048)
def analyze(text: str) -> dict:
    """Sentiment scores for one review. Returns VADER's four scores."""
    if not text or not text.strip():
        return {"compound": 0.0, "pos": 0.0, "neu": 1.0, "neg": 0.0}
    return _analyzer().polarity_scores(text)


def compound(text: str) -> float:
    """Just the compound score in [-1, 1]."""
    return analyze(text)["compound"]


def label(compound_score: float) -> str:
    """Human-readable sentiment bucket."""
    if compound_score >= 0.35:
        return "positive"
    if compound_score <= -0.35:
        return "negative"
    return "mixed"


def aggregate(reviews) -> dict:
    """Summary stats over an iterable of Review rows (or dicts with scores).

    Returns distribution buckets, averages, and the strongest quotes.
    """
    rows = []
    for r in reviews:
        c = r["sentiment_compound"] if isinstance(r, dict) else r.sentiment_compound
        content = r["content"] if isinstance(r, dict) else r.content
        rows.append((float(c or 0.0), content or ""))

    if not rows:
        return {
            "count": 0,
            "avg_compound": 0.0,
            "distribution": {"positive": 0, "mixed": 0, "negative": 0},
            "most_positive": None,
            "most_negative": None,
        }

    compounds = [c for c, _ in rows]
    distribution = {"positive": 0, "mixed": 0, "negative": 0}
    for c in compounds:
        distribution[label(c)] += 1

    by_compound = sorted(rows, key=lambda x: x[0])
    clip = lambda s: (s[:140] + "…") if len(s) > 141 else s

    return {
        "count": len(rows),
        "avg_compound": round(sum(compounds) / len(compounds), 3),
        "distribution": distribution,
        "most_positive": {"compound": by_compound[-1][0], "quote": clip(by_compound[-1][1])},
        "most_negative": {"compound": by_compound[0][0], "quote": clip(by_compound[0][1])},
    }
