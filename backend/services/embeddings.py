"""Embedding providers for movie and query text.

Two interchangeable backends produce unit-length vectors:

* ``MiniLMProvider``  — sentence-transformers/all-MiniLM-L6-v2 (384-dim neural).
* ``LSAProvider``     — TF-IDF + truncated SVD over the local catalog
                        (a classic latent-semantic-analysis pipeline).

The active backend is recorded in ``app_meta``; if it changes (or the catalog
is embedded for the first time) every movie is re-embedded automatically.
"""

import hashlib
import json
import logging
import threading
from functools import lru_cache

import numpy as np

from models.database import AppMeta, MovieCache, get_meta, set_meta

logger = logging.getLogger("cinematch.embeddings")

SIGNATURE_KEY = "embedding_signature"

EMBED_DIM = 384

_lock = threading.Lock()
_provider_cache = {}


def movie_text(title: str, overview: str, genres) -> str:
    """Canonical text representation embedded for every movie."""
    genre_str = ", ".join(g for g in (genres or []) if g)
    return f"{title}. {overview or ''} Genres: {genre_str}."


class MiniLMProvider:
    """Neural sentence embeddings via sentence-transformers."""

    name = "minilm-all-MiniLM-L6-v2"
    dim = EMBED_DIM
    requires_fit = False

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("all-MiniLM-L6-v2")

    @property
    def signature(self):
        return f"{self.name}:{self.dim}"

    def encode(self, texts):
        vecs = self._model.encode(
            list(texts),
            batch_size=64,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(vecs, dtype=np.float32)

    def encode_one(self, text):
        return self.encode([text])[0]


class LSAProvider:
    """TF-IDF + TruncatedSVD latent semantic embeddings (no downloads needed)."""

    requires_fit = True

    def __init__(self, corpus):
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import Normalizer

        n_docs = len(corpus)
        n_components = int(min(EMBED_DIM, max(2, n_docs - 1)))
        self._pipeline = make_pipeline(
            TfidfVectorizer(
                sublinear_tf=True,
                strip_accents="unicode",
                stop_words="english",
                ngram_range=(1, 2),
                min_df=1,
            ),
            TruncatedSVD(n_components=n_components, random_state=42),
            Normalizer(copy=False),
        )
        self._pipeline.fit(list(corpus))
        self.dim = n_components
        # The fitted space depends on the exact document set: adding movies
        # changes vocabulary and SVD basis. Fingerprint the corpus so the
        # signature changes with it — otherwise new vectors are mixed into an
        # old space (drift) and stored vectors stop being comparable.
        self._corpus_fingerprint = hashlib.sha1(
            "\n".join(sorted(corpus)).encode("utf-8", "ignore")
        ).hexdigest()[:12]
        # Keep the vocabulary around so unseen query terms degrade gracefully.
        self.name = f"lsa-tfidf-svd{n_components}"

    @property
    def signature(self):
        return f"{self.name}:{self.dim}:{self._corpus_fingerprint}"

    def encode(self, texts):
        return np.asarray(self._pipeline.transform(list(texts)), dtype=np.float32)

    def encode_one(self, text):
        return self.encode([text])[0]


def _build_provider(corpus):
    """Prefer neural MiniLM; fall back to LSA when torch/sentence-transformers
    are unavailable."""
    try:
        import sentence_transformers  # noqa: F401

        return MiniLMProvider()
    except Exception as exc:  # ImportError, download failure, …
        logger.warning("sentence-transformers unavailable (%s); using LSA fallback", exc)
    if not corpus:
        raise RuntimeError("No catalog text available to fit the LSA embedding space.")
    return LSAProvider(corpus)


def get_provider(db):
    """Return the active embedding provider (cached process-wide)."""
    with _lock:
        cached = _provider_cache.get("current")
        if cached is not None:
            return cached
        corpus = [
            movie_text(m.title, m.overview, (m.genres or "").split(","))
            for m in db.query(MovieCache.title, MovieCache.overview, MovieCache.genres).all()
        ]
        try:
            provider = _build_provider(corpus)
        except Exception as exc:
            logger.error("Could not initialize embedding provider: %s", exc)
            raise
        _provider_cache["current"] = provider
        return provider


def active_signature(db):
    return get_meta(db, SIGNATURE_KEY)


def embed_movies(db, batch_size=128, force=False):
    """Embed every cached movie whose vector is missing or stale.

    Returns {"embedded": n, "provider": signature}.
    """
    provider = get_provider(db)
    signature = provider.signature

    movies = db.query(MovieCache).all()
    todo = []
    if force or active_signature(db) != signature:
        todo = movies  # provider changed → recompute everything
    else:
        for m in movies:
            vec = _decode(m.embedding)
            if vec is None or len(vec) != provider.dim:
                todo.append(m)

    if not todo:
        set_meta(db, SIGNATURE_KEY, signature)
        return {"embedded": 0, "provider": signature}

    logger.info("Embedding %d movies with %s", len(todo), signature)
    for start in range(0, len(todo), batch_size):
        chunk = todo[start : start + batch_size]
        texts = [
            movie_text(m.title, m.overview, (m.genres or "").split(","))
            for m in chunk
        ]
        vectors = provider.encode(texts)
        for movie, vec in zip(chunk, vectors):
            movie.embedding = json.dumps([round(float(x), 5) for x in vec])
        db.commit()

    set_meta(db, SIGNATURE_KEY, signature)
    return {"embedded": len(todo), "provider": signature}


def embed_query(db, text):
    """Embed an arbitrary search query into the same space as the catalog."""
    provider = get_provider(db)
    return provider.encode_one(text)


def decode_embedding(json_str):
    """Deserialize a stored embedding; returns np.float32 array or None."""
    return _decode(json_str)


@lru_cache(maxsize=30000)
def _decode(json_str):
    """Deserialize one embedding JSON string.

    Memoized: the feed/search paths decode the same ~2k embeddings on every
    request. Returned arrays are shared — callers must treat them as
    read-only (all current callers only compute similarities)."""
    if not json_str:
        return None
    try:
        data = json.loads(json_str)
        arr = np.asarray(data, dtype=np.float32)
        return arr if arr.size else None
    except (ValueError, TypeError):
        return None


def cosine(a, b):
    """Cosine similarity between two vectors (0.0 if either is degenerate)."""
    if a is None or b is None:
        return 0.0
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def clip_cosine(a, b):
    """Cosine similarity clipped to [0, 1] — the shared "honest" affinity.

    Match scores must not reward dissimilarity: the previous affine mapping
    (cos + 1) / 2 put unrelated content near 50% ("Semantically related"),
    which made garbage queries look meaningful. Negative cosine carries no
    match meaning for display, so it floors at zero.
    """
    return max(0.0, cosine(a, b))
