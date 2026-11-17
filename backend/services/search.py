"""Hybrid search.

Three query modes, chosen automatically:

1. anchor  — "movies like Inception": rank by proximity to that film
             (embedding similarity + CF co-rating evidence when it exists).
2. mood    — "something to make me cry": feature-based scoring against a
             small set of interpretable mood profiles (genres + overview
             language). No hardcoded title lists.
3. general — everything else: semantic embedding cosine + lexical overlap,
             with an optional personalization nudge.
"""

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache

from models.database import FilmNeighbor, FilmPerson
from services.embeddings import clip_cosine, decode_embedding, embed_query

ANCHOR_PATTERNS = re.compile(
    r"^\s*(?:something\s+|anything\s+|movies?\s+|films?\s+)?"
    r"(?:like|similar to|as good as)\s+(?P<title>.+?)\s*$",
    re.IGNORECASE,
)

TITLE_YEAR_RE = re.compile(
    r"^\s*(?P<title>.+?)\s*(?:\((?P<year_paren>\d{4})\)|(?P<year_plain>\d{4}))\s*$"
)

# Mood profiles: interpretable feature weights, not title lists.
MOOD_PROFILES = {
    "cry": {
        "genres": {"Drama": 1.0, "Romance": 0.6, "War": 0.6, "Music": 0.3},
        "terms": {"death", "grief", "loss", "love", "sacrifice", "illness",
                  "orphan", "farewell", "tragedy", "memory", "widow", "terminal"},
    },
    "cheer_up": {
        "genres": {"Comedy": 1.0, "Family": 0.8, "Animation": 0.8, "Adventure": 0.5, "Music": 0.4},
        "terms": {"friendship", "friend", "dream", "hope", "magical", "whimsical",
                  "joy", "sing", "dance", "heartwarming", "adventure", "summer"},
    },
    "funny": {
        "genres": {"Comedy": 1.2},
        "terms": {"comedy", "hilarious", "party", "misadventure", "awkward",
                  "goofy", "satire", "prank", "buddy"},
    },
    "romantic": {
        "genres": {"Romance": 1.2, "Drama": 0.4},
        "terms": {"love", "romance", "couple", "passion", "wedding", "affair",
                  "relationship", "kiss"},
    },
    "mind_bending": {
        "genres": {"Science Fiction": 0.9, "Mystery": 0.8, "Thriller": 0.5},
        "terms": {"dream", "memory", "identity", "illusion", "time", "reality",
                  "paradox", "subconscious", "twist", "psychological"},
    },
    "epic_adventure": {
        "genres": {"Adventure": 1.0, "Fantasy": 0.8, "Action": 0.6},
        "terms": {"quest", "journey", "kingdom", "battle", "legend", "hero",
                  "empire", "myth", "fellowship"},
    },
    "nostalgic": {
        "genres": {"Family": 0.9, "Animation": 0.7, "Comedy": 0.5, "Drama": 0.4},
        "terms": {"childhood", "coming-of-age", "school", "summer", "friends",
                  "hometown", "memory", "growing"},
    },
    "dark_atmospheric": {
        "genres": {"Thriller": 0.9, "Crime": 0.7, "Mystery": 0.6, "Horror": 0.5},
        "terms": {"murder", "detective", "obsession", "corruption", "noir",
                  "haunting", "revenge", "serial", "conspiracy", "dread"},
    },
}

MOOD_TRIGGERS = {
    "cry": {"cry", "cried", "crying", "sad", "tears", "sob", "weep", "emotional",
            "heartbreaking", "devastating", "tearjerker"},
    "cheer_up": {"cheer", "happy", "uplift", "uplifting", "comfort", "feel-good",
                 "feelgood", "smile", "warm"},
    "funny": {"funny", "comedy", "laugh", "hilarious", "goofy", "silly", "humor"},
    "romantic": {"romantic", "romance", "date", "love-story"},
    "mind_bending": {"mind-bending", "mindbending", "twist", "psychological",
                     "confusing", "trippy", "weird"},
    "epic_adventure": {"epic", "adventure", "quest", "grand", "fantasy"},
    "nostalgic": {"nostalgic", "nostalgia", "childhood", "cozy", "old-school"},
    "dark_atmospheric": {"dark", "atmospheric", "moody", "noir", "bleak",
                         "haunting", "brooding", "gritty"},
}


def clean_title(title):
    return re.sub(r"[^\w\s]", "", (title or "").lower()).strip()


def _tokens(text):
    stop = {
        "a", "an", "and", "are", "as", "for", "i", "in", "is", "it", "like", "me",
        "movie", "movies", "film", "films", "of", "on", "or", "something", "that",
        "the", "to", "want", "watch", "with", "recommend", "give", "show", "any",
    }
    words = re.findall(r"[a-z\-']+", (text or "").lower())
    return {w for w in words if w not in stop}


def detect_mood(query):
    tokens = _tokens(query)
    for mood, triggers in MOOD_TRIGGERS.items():
        if tokens & triggers:
            return mood
    return None


def parse_title_year(query):
    """Split a bare ``Title (YYYY)`` / ``Title YYYY`` query.

    Anchor phrases ("movies like X") and mood queries deliberately return
    ``(query, None)`` so they never masquerade as exact title lookups.
    """
    text = (query or "").strip()
    if not text or ANCHOR_PATTERNS.match(text) or detect_mood(text):
        return text, None
    match = TITLE_YEAR_RE.match(text)
    if not match:
        return text, None
    year = match.group("year_paren") or match.group("year_plain")
    return match.group("title").strip(), int(year) if year else None


def find_exact_movie(query, movies):
    """Return the catalog row whose title exactly matches the query.

    A bare title is accepted only when it is unique in the catalog. Adding a
    year disambiguates remakes; when several rows share the title/year, the
    highest-vote/popularity row wins (the same order TMDB uses).
    """
    title, year = parse_title_year(query)
    wanted = clean_title(title)
    if len(wanted) < 3:
        return None
    candidates = [movie for movie in movies if clean_title(movie.title) == wanted]
    if year is not None:
        candidates = [
            movie for movie in candidates if getattr(movie, "release_year", None) == year
        ]
    if len(candidates) == 1:
        return candidates[0]
    if candidates and year is not None:
        return max(
            candidates,
            key=lambda movie: (
                getattr(movie, "vote_average", 0) or 0,
                getattr(movie, "popularity", 0) or 0,
            ),
        )
    return None


def detect_anchor(query, movies):
    """Fuzzy-match the query against catalog titles → MovieCache row or None."""
    stripped = ANCHOR_PATTERNS.match(query or "")
    target = stripped.group("title") if stripped else query
    cleaned = clean_title(target)
    if not cleaned or len(cleaned) < 3:
        return None

    best, best_ratio = None, 0.0
    for movie in movies:
        t = clean_title(movie.title)
        if not t:
            continue
        if t == cleaned:
            return movie
        ratio = SequenceMatcher(None, cleaned, t).ratio()
        # Substring bonus, but only for real titles. Single/double-letter
        # titles ("Z", "M", "Pi") otherwise match almost any query
        # containing those letters and fabricate an anchor.
        if len(t) >= 4 and len(cleaned) >= 3 and (cleaned in t or t in cleaned):
            ratio = max(ratio, 0.86)
        if ratio > best_ratio:
            best, best_ratio = movie, ratio
    return best if best_ratio >= 0.82 else None


def _mood_score(movie, profile):
    genres = {g.strip() for g in (movie.genres or "").split(",") if g.strip()}
    text = f"{movie.overview or ''}".lower()
    genre_score = sum(w for g, w in profile["genres"].items() if g in genres)
    term_hits = sum(1 for term in profile["terms"] if term in text)
    total_max = sum(profile["genres"].values()) + 3.0
    raw = genre_score + term_hits * 0.5
    return min(1.0, raw / total_max)


def _lexical_score(query_tokens, movie):
    """Cheap lexical relevance: token overlap with title/overview/genres."""
    if not query_tokens:
        return 0.0
    title = clean_title(movie.title)
    overview = (movie.overview or "").lower()
    genres = (movie.genres or "").lower()
    hits_title = sum(1 for t in query_tokens if t in title)
    hits_body = sum(1 for t in query_tokens if t in overview or t in genres)
    score = hits_title * 0.35 + min(hits_body * 0.12, 0.45)
    if query_tokens and title and all(t in title for t in query_tokens):
        score += 0.4
    return min(1.0, score)


def _tag_raw_key(raw):
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, sort_keys=True)
    except (TypeError, ValueError):
        return str(raw)


@lru_cache(maxsize=30000)
def _parse_movie_tags(themes_raw, keywords_raw):
    tags = set()
    for raw, prefix in ((themes_raw, ""), (keywords_raw, "kw:")):
        if not raw:
            continue
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        for value in values or []:
            if isinstance(value, dict):
                value = value.get("id") or value.get("label")
            if not value:
                continue
            text = str(value).strip().lower()
            if text:
                tags.add(prefix + text)
    return tags


def _movie_tags(movie):
    """Theme and keyword identities used for franchise/relationship evidence.

    Letterboxd themes and TMDB keywords are stored as JSON arrays. Some rows
    contain either strings or ``{id,label}`` objects, so normalize both shapes
    into one namespaced set. This is descriptive metadata only; no title lists.
    Parsing is cached because every search would otherwise re-decode the whole
    catalog just to score its relationship evidence.
    """
    return _parse_movie_tags(
        _tag_raw_key(getattr(movie, "themes", None)),
        _tag_raw_key(getattr(movie, "keywords", None)),
    )


def _anchor_relationship_context(db, anchor, movies):
    """Load the evidence that makes two films related, not merely similar.

    Collaborative filtering answers "people who rated this also rated that";
    it does not know that The Dark Knight Rises is the direct sequel. This
    context adds same-collection, shared people, and shared franchise/tag
    evidence. Only people attached to the anchor are queried, so the work stays
    bounded by one film's credits rather than the whole catalog.
    """
    anchor_people = {
        person_id
        for (person_id,) in db.query(FilmPerson.person_id)
        .filter(FilmPerson.tmdb_id == anchor.tmdb_id)
        .all()
    }
    shared_people = {}
    if anchor_people:
        rows = (
            db.query(FilmPerson.tmdb_id, FilmPerson.person_id, FilmPerson.role)
            .filter(
                FilmPerson.person_id.in_(anchor_people),
                FilmPerson.tmdb_id != anchor.tmdb_id,
            )
            .all()
        )
        for tmdb_id, person_id, role in rows:
            shared_people.setdefault(tmdb_id, {})[person_id] = role

    collection_id = getattr(anchor, "collection_id", None)
    core_franchise_tags = set()
    if collection_id:
        collection = [m for m in movies if m.collection_id == collection_id]
        if len(collection) >= 2:
            tag_sets = [_movie_tags(m) for m in collection]
            if all(tag_sets):
                core_franchise_tags = set.intersection(*tag_sets)

    return {
        "tags": _movie_tags(anchor),
        "core_tags": core_franchise_tags,
        "people": shared_people,
        "collection_id": collection_id,
    }


def _relationship_score(movie, context):
    """Bounded 0–1 relationship evidence with human-readable signals.

    Weights intentionally put explicit franchise/credit evidence first.
    Semantic embedding and CF remain the base score; this is a bounded lift,
    not a replacement for them.
    """
    people = context["people"].get(movie.tmdb_id, {})
    roles = set(people.values())
    same_collection = bool(
        context["collection_id"]
        and getattr(movie, "collection_id", None) == context["collection_id"]
    )
    same_director = "director" in roles
    same_writer = "writer" in roles
    cast_overlap = sum(1 for role in people.values() if role == "cast")

    movie_tags = _movie_tags(movie)
    shared_tags = context["tags"] & movie_tags
    if context["tags"] and movie_tags:
        tag_overlap = len(shared_tags) / min(len(context["tags"]), len(movie_tags))
    else:
        tag_overlap = 0.0

    # Tags shared by every film in the anchor's collection describe the
    # franchise's world (e.g. "superhero", "based on comic"), not just one
    # film. A cousin from a different collection (Batman Beyond) can share
    # that world even when it has no direct cast/crew overlap.
    same_franchise_world = bool(
        context["core_tags"] & movie_tags and not same_collection
    )

    relation = min(
        1.0,
        0.35 * same_collection
        + 0.18 * same_director
        + 0.12 * min(cast_overlap, 3) / 3
        + 0.04 * same_writer
        + 0.16 * min(1.0, tag_overlap * 2.0)
        + 0.20 * same_franchise_world,
    )

    if same_collection:
        signal = "collection"
    elif same_director:
        signal = "director"
    elif cast_overlap:
        signal = "cast"
    elif same_franchise_world:
        signal = "franchise"
    elif same_writer:
        signal = "writer"
    elif tag_overlap >= 0.30:
        signal = "themes"
    else:
        signal = None

    return relation, signal


def _relationship_reason(signal, anchor_title):
    if signal == "collection":
        return f"Continues the same franchise as {anchor_title}"
    if signal == "director":
        return f"From the same director as {anchor_title}"
    if signal == "cast":
        return f"Shares cast with {anchor_title}"
    if signal == "franchise":
        return f"Belongs to the same film family as {anchor_title}"
    if signal == "writer":
        return f"From the same writing team as {anchor_title}"
    if signal == "themes":
        return f"Shares its franchise themes with {anchor_title}"
    return None


def _behavior_reason(evidence_raw, anchor_title):
    """Human-readable evidence for a local ``behavior_v1`` edge."""
    try:
        evidence = json.loads(evidence_raw) if evidence_raw else {}
    except (TypeError, ValueError):
        evidence = {}
    signals = evidence.get("signals") if isinstance(evidence, dict) else {}
    signals = signals or {}
    hop = int(signals.get("hop") or 1)
    if hop > 1:
        return (
            f"Connected to {anchor_title} through {hop} hops of "
            "local viewer behavior"
        )
    co_likers = int(signals.get("co_likers") or 0)
    co_raters = int(signals.get("co_raters") or 0)
    if co_likers >= 2:
        return f"{co_likers} people who liked {anchor_title} also liked this"
    if co_raters >= 2:
        return f"{co_raters} people who rated {anchor_title} also rated this"
    return f"Linked by local viewers of {anchor_title}"


def _taste_nudge(taste_vector, taste_modes, emb):
    """Best personalization affinity: the global vector or any taste mode.

    A comedy should be nudged by the user's comedy side even when their
    overall centroid leans elsewhere — same reasoning as the feed's
    max-over-modes content score.
    """
    sim = clip_cosine(taste_vector, emb) if taste_vector is not None else 0.0
    for mode in taste_modes or []:
        sim = max(sim, clip_cosine(mode.vector, emb))
    return sim


def search(db, query, movies, taste_vector=None, limit=16, anchor=None, taste_modes=None):
    """Main entry. Returns (mode, label, results) where results is a list of
    {"movie": row, "score": 0-100, "reason": str}.

    ``anchor`` lets the router pass an exact title match it already resolved
    (including a ``Title (YYYY)`` query that fuzzy anchor detection misses).
    ``taste_modes`` are optional :class:`taste.TasteMode` centroids used for
    the personalization nudge in mood and general search.
    """
    query_vec = embed_query(db, query)
    query_tokens = _tokens(query)
    mood = detect_mood(query)
    if anchor is None and not mood:
        anchor = detect_anchor(query, movies)

    results = []

    if anchor is not None:
        mode = "anchor"
        # CF co-rating similarity, when the matrix supports it (cached).
        from services import collab

        _, sims, _ = collab.get_model(db)
        cf_neighbors = dict(collab.similar_films(anchor.tmdb_id, sims, limit=200))
        behavior_neighbors = {
            int(neighbor_id): (float(score), evidence)
            for neighbor_id, score, evidence in db.query(
                FilmNeighbor.neighbor_tmdb_id,
                FilmNeighbor.score,
                FilmNeighbor.evidence,
            )
            .filter(
                FilmNeighbor.tmdb_id == anchor.tmdb_id,
                FilmNeighbor.source == "behavior_v1",
            )
            .order_by(FilmNeighbor.rank)
            .limit(80)
            .all()
        }
        anchor_vec = decode_embedding(anchor.embedding)
        relationship_context = _anchor_relationship_context(db, anchor, movies)

        for movie in movies:
            if movie.tmdb_id == anchor.tmdb_id:
                continue
            emb = decode_embedding(movie.embedding)
            # Proximity to the anchor *film* leads; the raw query string
            # (usually just the title) only nudges. Previously the query
            # string vector was the only signal, so "inception" ranked films
            # by the word "inception" rather than by Inception itself.
            sim_query = clip_cosine(query_vec, emb)
            if anchor_vec is not None:
                sim = 0.7 * clip_cosine(anchor_vec, emb) + 0.3 * sim_query
            else:
                sim = sim_query

            behavior = behavior_neighbors.get(movie.tmdb_id)
            cf_sim = cf_neighbors.get(movie.tmdb_id)
            if behavior is not None:
                behavior_score, behavior_evidence = behavior
                # Local co-rating/co-like/co-watch evidence leads.  The
                # embedding remains a tie-breaker, not the relationship.
                score = 0.28 * sim + 0.72 * min(1.0, behavior_score / 0.55)
                reason = _behavior_reason(behavior_evidence, anchor.title)
            elif cf_sim is not None:
                score = 0.55 * sim + 0.45 * min(1.0, cf_sim / 0.5)
                reason = f"Raters who liked {anchor.title} also liked this"
            else:
                score = sim
                reason = f"Shares tone and themes with {anchor.title}"

            relation, signal = _relationship_score(movie, relationship_context)
            if relation:
                # Explicit franchise/credit evidence is a bounded lift over
                # the semantic and behavior bases.  It must not overwrite a
                # stronger local behavior edge.
                score = min(1.0, score + (0.10 if behavior else 0.20) * relation)
                relation_reason = _relationship_reason(signal, anchor.title)
                if relation_reason and behavior is None:
                    reason = relation_reason

            results.append({"movie": movie, "raw": max(0.0, min(1.0, score)), "reason": reason})
        label = f"because you searched for “{anchor.title}”"

    elif mood is not None:
        mode = "mood"
        profile = MOOD_PROFILES[mood]
        for movie in movies:
            emb = decode_embedding(movie.embedding)
            semantic = clip_cosine(query_vec, emb) if emb is not None else 0.0
            mood_s = _mood_score(movie, profile)
            quality = (movie.vote_average or 0) / 10.0
            score = 0.40 * mood_s + 0.30 * semantic + 0.15 * quality
            if taste_vector is not None or taste_modes:
                score += 0.15 * _taste_nudge(taste_vector, taste_modes, emb)
            else:
                score += 0.15 * semantic
            top_genre = next(
                (g for g, w in sorted(profile["genres"].items(), key=lambda kv: -kv[1])
                 if g in {x.strip() for x in (movie.genres or "").split(",")}),
                None,
            )
            reason = f"Matches your “{mood.replace('_', ' ')}” mood" + (
                f" — {top_genre}" if top_genre else ""
            )
            results.append({"movie": movie, "raw": max(0.0, min(1.0, score)), "reason": reason})
        label = f"mood match: {mood.replace('_', ' ')}"

    else:
        mode = "general"
        for movie in movies:
            emb = decode_embedding(movie.embedding)
            semantic = clip_cosine(query_vec, emb) if emb is not None else 0.0
            lexical = _lexical_score(query_tokens, movie)
            score = 0.60 * semantic + 0.25 * lexical + 0.15 * ((movie.vote_average or 0) / 10.0)
            if taste_vector is not None or taste_modes:
                score = 0.88 * score + 0.12 * _taste_nudge(taste_vector, taste_modes, emb)
            raw = max(0.0, min(1.0, score))
            # Only claim semantic relevance when there is some. A query with
            # no vocabulary overlap and near-zero affinity should not be
            # dressed up as a ~45% "related" result.
            if lexical > 0.3 and raw >= 0.35:
                reason = "Strong match on meaning and language of your query"
            elif raw >= 0.30:
                reason = "Semantically related to your query"
            else:
                reason = "No strong match — loosely related suggestion"
            results.append({"movie": movie, "raw": raw, "reason": reason})
        label = "semantic + lexical relevance"

    results.sort(key=lambda r: r["raw"], reverse=True)
    out = [
        {
            "movie": r["movie"],
            "score": round(r["raw"] * 100.0, 1),
            "reason": r["reason"],
        }
        for r in results[:limit]
    ]
    return mode, label, out
