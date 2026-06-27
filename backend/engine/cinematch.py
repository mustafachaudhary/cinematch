#!/usr/bin/env python3
"""
CineMatch ML Engine — Content-Based Movie Recommendation System
===============================================================
A single-file implementation that builds mathematical taste profiles from user ratings
and film features, returning ranked recommendations with explanations.
"""

import json
import os
import re
import warnings
from typing import Iterable, Optional

import numpy as np
import pandas as pd
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.preprocessing import MinMaxScaler, MultiLabelBinarizer, normalize
from sklearn.metrics.pairwise import cosine_similarity

warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION BLOCK — All tunable parameters live here, nowhere else
# ============================================================================
CONFIG = {
    # Feature weights in item matrix
    "w_genre": 1.0,
    "w_theme": 2.5,
    "w_year": 0.5,

    # User vector construction
    "aversion_weight": 0.35,

    # Scoring
    "score_floor": 0.10,
    "apply_genre_filter": True,
    "genre_filter_threshold": 0.8,

    # MMR diversity
    "mmr_lambda": 0.70,

    # Output
    "top_n": 20,

    # Sentence transformer model
    "embedding_model": "all-MiniLM-L6-v2",

    # Cache path for item matrix
    "cache_path": "item_matrix_cache.npz",

    # Hybrid scoring (for future collaborative filtering)
    "alpha": 0.0,  # 0.0 = pure content-based, 1.0 = pure collaborative
}

# Global cache for sentence transformer (loaded once)
_sentence_model_cache = None


# ============================================================================
# MODULE 1 — Data Loading & Merging
# ============================================================================
def load_data(ratings_path: str, themes_path: str) -> pd.DataFrame:
    """
    Load and merge ratings CSV with themes JSON.
    
    Returns DataFrame with columns: film_id, Name, Year, Rating, genres, themes
    """
    # Load ratings
    ratings_df = pd.read_csv(ratings_path)
    ratings_df = ratings_df[['Name', 'Year', 'Rating']].copy()
    
    # Load themes JSON
    with open(themes_path, 'r', encoding='utf-8') as f:
        themes_data = json.load(f)
    
    # Filter to successful entries only and convert to DataFrame
    successful = {
        name: data for name, data in themes_data.items() 
        if data.get('status') == 'Success'
    }
    
    themes_df = pd.DataFrame([
        {
            'Name': name.strip(),  # Strip whitespace from keys
            'genres': data.get('genres', []),
            'themes': data.get('themes', [])
        }
        for name, data in successful.items()
    ])
    
    # Strip whitespace from ratings Name column for matching
    ratings_df['Name'] = ratings_df['Name'].str.strip()
    
    # Inner join on Name
    merged = pd.merge(ratings_df, themes_df, on='Name', how='inner')
    
    # Reset index and add film_id
    merged = merged.reset_index(drop=True)
    merged['film_id'] = merged.index.astype(int)
    
    print(f"Merged data: {len(merged)} films survived the join")
    
    return merged[['film_id', 'Name', 'Year', 'Rating', 'genres', 'themes']]


def _split_genres(value) -> list[str]:
    """Normalize genres from database strings, JSON-ish strings, or lists."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _overview_keywords(text: str, limit: int = 18) -> list[str]:
    """Create lightweight theme tokens when Letterboxd theme data is unavailable."""
    if not text:
        return []
    stop_words = {
        "about", "after", "again", "against", "along", "also", "because", "before",
        "between", "their", "there", "these", "those", "through", "under", "where",
        "while", "with", "when", "from", "into", "over", "they", "them", "this",
        "that", "will", "must", "have", "has", "his", "her", "and", "the",
    }
    words = re.findall(r"\b[a-zA-Z][a-zA-Z-]{3,}\b", text.lower())
    seen = set()
    keywords = []
    for word in words:
        if word in stop_words or word in seen:
            continue
        seen.add(word)
        keywords.append(word)
        if len(keywords) >= limit:
            break
    return keywords


def movies_to_dataframe(movies: Iterable, include_rating: bool = False) -> pd.DataFrame:
    """
    Convert backend ORM movie objects into the engine's feature DataFrame.

    LoggedMovie objects provide Rating; MovieCache candidates do not. When
    Letterboxd themes are not present, overview keywords act as the semantic
    theme text for the sentence-transformer track.
    """
    rows = []
    for idx, movie in enumerate(movies):
        title = getattr(movie, "title", None)
        if not title:
            continue
        genres = _split_genres(getattr(movie, "genres", None))
        overview = getattr(movie, "overview", "") or ""
        themes = _overview_keywords(overview)
        if not themes:
            themes = genres

        row = {
            "film_id": len(rows),
            "source_id": getattr(movie, "tmdb_id", None),
            "Name": title,
            "Year": getattr(movie, "release_year", None) or 0,
            "genres": genres,
            "themes": themes,
            "_movie": movie,
        }
        if include_rating:
            row["Rating"] = float(getattr(movie, "rating", 0) or 0)
        rows.append(row)

    columns = ["film_id", "source_id", "Name", "Year", "genres", "themes", "_movie"]
    if include_rating:
        columns.insert(3, "Rating")
    return pd.DataFrame(rows, columns=columns)


# ============================================================================
# MODULE 2 — Feature Engineering
# ============================================================================
def build_feature_tracks(df: pd.DataFrame):
    """
    Build three separate feature matrices: genres (binary), themes (dense semantic), year (scalar).
    
    Returns: (genres_matrix, themes_matrix, year_matrix, genre_names, sentence_model)
    """
    global _sentence_model_cache
    
    N = len(df)
    
    # Track A — Genres (binary)
    mlb = MultiLabelBinarizer()
    genres_matrix = mlb.fit_transform(df['genres']).astype(np.float32)
    genre_names = list(mlb.classes_)
    
    # Track B — Themes (dense semantic via sentence transformer when available,
    # otherwise a deterministic local text vectorizer so the backend remains light.
    theme_strings = [', '.join(themes) if themes else '' for themes in df['themes']]
    if SentenceTransformer is None:
        vectorizer = HashingVectorizer(
            n_features=384,
            alternate_sign=False,
            norm="l2",
            token_pattern=r"(?u)\b\w[\w-]+\b",
        )
        themes_matrix = vectorizer.transform(theme_strings).astype(np.float32).toarray()
        model = None
    elif _sentence_model_cache is None:
        print(f"Loading sentence transformer: {CONFIG['embedding_model']}")
        _sentence_model_cache = SentenceTransformer(CONFIG['embedding_model'])
        model = _sentence_model_cache
        themes_embeddings = model.encode(theme_strings, show_progress_bar=True)
        themes_matrix = normalize(themes_embeddings, norm='l2').astype(np.float32)
    else:
        model = _sentence_model_cache
        themes_embeddings = model.encode(theme_strings, show_progress_bar=True)
        themes_matrix = normalize(themes_embeddings, norm='l2').astype(np.float32)
    
    # Track C — Year (scalar, min-max scaled to [0, 1])
    year_scaler = MinMaxScaler()
    years = df['Year'].fillna(0).values.reshape(-1, 1)
    year_matrix = year_scaler.fit_transform(years).astype(np.float32)
    
    return genres_matrix, themes_matrix, year_matrix, genre_names, model


# ============================================================================
# MODULE 3 — Item Matrix Construction
# ============================================================================
def build_item_matrix(genres_matrix: np.ndarray, themes_matrix: np.ndarray,
                      year_matrix: np.ndarray, cache_path: Optional[str]) -> np.ndarray:
    """
    Build weighted item matrix with caching support.
    
    IMPORTANT: If genres or themes data changes (re-scrape, new films added),
    the cache file must be deleted manually before re-running.
    """
    # Check cache
    if cache_path and os.path.exists(cache_path):
        print("Loaded item matrix from cache.")
        data = np.load(cache_path)
        return data['matrix'].astype(np.float32)
    
    # Apply weights from CONFIG
    weighted_genres = CONFIG['w_genre'] * genres_matrix
    weighted_themes = CONFIG['w_theme'] * themes_matrix
    weighted_year = CONFIG['w_year'] * year_matrix
    
    # Horizontal stack
    item_matrix = np.hstack([weighted_genres, weighted_themes, weighted_year])
    item_matrix = item_matrix.astype(np.float32)
    
    # Save to cache
    if cache_path:
        np.savez(cache_path, matrix=item_matrix)
    
    print(f"Built item matrix with shape: {item_matrix.shape}")
    
    return item_matrix


# ============================================================================
# MODULE 4 — User Preference Vector
# ============================================================================
def build_user_vector(df: pd.DataFrame, item_matrix: np.ndarray) -> np.ndarray:
    """
    Build user preference vector from ratings and item matrix.
    
    This is the most important function: computes weighted average of liked films
    minus weighted average of disliked films (scaled by aversion_weight).
    """
    ratings = df['Rating'].values.astype(np.float32)
    mean_rating = np.mean(ratings)
    print(f"Mean user rating: {mean_rating:.3f}")
    
    centered_weights = ratings - mean_rating
    
    # Positive vector (liked films)
    positive_mask = centered_weights > 0
    if np.any(positive_mask):
        positive_films = item_matrix[positive_mask]
        positive_weights = centered_weights[positive_mask]
        positive_vector = np.average(positive_films, axis=0, weights=positive_weights)
    else:
        positive_vector = np.zeros(item_matrix.shape[1], dtype=np.float32)
    
    # Negative vector (disliked films)
    negative_mask = centered_weights < 0
    if np.any(negative_mask):
        negative_films = item_matrix[negative_mask]
        negative_weights = np.abs(centered_weights[negative_mask])
        negative_vector = np.average(negative_films, axis=0, weights=negative_weights)
    else:
        negative_vector = np.zeros(item_matrix.shape[1], dtype=np.float32)
    
    # Combine with aversion weighting
    user_vector = positive_vector - (CONFIG['aversion_weight'] * negative_vector)
    
    # L2 normalize
    norm = np.linalg.norm(user_vector)
    if norm > 0:
        user_vector = user_vector / norm
    
    # Reshape for cosine_similarity compatibility
    return user_vector.reshape(1, -1).astype(np.float32)


# ============================================================================
# MODULE 5 — Similarity Scoring & Filtering
# ============================================================================
def score_films(user_vector: np.ndarray, item_matrix: np.ndarray, 
                df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute similarity scores and apply filtering.
    """
    # Compute cosine similarities
    scores = cosine_similarity(user_vector, item_matrix).flatten()
    
    # Add to DataFrame copy
    candidates = df.copy()
    candidates['similarity_score'] = scores
    
    # Apply score floor
    candidates = candidates[candidates['similarity_score'] >= CONFIG['score_floor']].copy()
    
    # Genre aversion filter (optional)
    if CONFIG['apply_genre_filter']:
        # Identify bottom 20% by rating
        threshold = df['Rating'].quantile(0.2)
        low_rated = df[df['Rating'] <= threshold]
        
        # Compute genre aversion: fraction of each genre in low_rated
        all_genres = set()
        for genres in df['genres']:
            all_genres.update(genres)
        
        aversion_genres = []
        for genre in all_genres:
            genre_films = df[df['genres'].apply(lambda g: genre in g)]
            if len(genre_films) == 0:
                continue
            low_fraction = len(low_rated[low_rated['genres'].apply(lambda g: genre in g)]) / len(genre_films)
            if low_fraction > CONFIG['genre_filter_threshold']:
                aversion_genres.append(genre)
        
        if aversion_genres:
            print(f"Flagged aversion genres: {aversion_genres}")
            
            # Remove films whose genres are ENTIRELY aversion genres
            def has_non_aversion(genres):
                return any(g not in aversion_genres for g in genres) if genres else True
            
            candidates = candidates[candidates['genres'].apply(has_non_aversion)].copy()
    
    # Sort by similarity descending
    candidates = candidates.sort_values('similarity_score', ascending=False).reset_index(drop=True)
    
    return candidates


# ============================================================================
# MODULE 6 — MMR Diversity Reranking
# ============================================================================
def apply_mmr(candidates_df: pd.DataFrame, item_matrix: np.ndarray, 
              top_n: int, mmr_lambda: float) -> pd.DataFrame:
    """
    Apply Maximal Marginal Relevance to balance relevance vs. redundancy.
    """
    if len(candidates_df) == 0:
        return candidates_df.head(0)
    
    # Extract candidate indices and scores
    candidate_ids = candidates_df['film_id'].values
    candidate_scores = candidates_df['similarity_score'].values
    
    # Get candidate vectors and L2 normalize
    candidate_vectors = item_matrix[candidate_ids]
    candidate_vectors = normalize(candidate_vectors, norm='l2')
    
    selected_ids = []
    selected_vectors = []
    mmr_scores = []
    
    remaining_mask = np.ones(len(candidate_ids), dtype=bool)
    
    while len(selected_ids) < top_n and remaining_mask.any():
        best_idx = -1
        best_mmr = -np.inf
        
        for idx in np.where(remaining_mask)[0]:
            relevance = candidate_scores[idx]
            
            if len(selected_vectors) == 0:
                redundancy = 0.0
            else:
                # Max cosine similarity to already selected
                sims = cosine_similarity(
                    candidate_vectors[idx:idx+1], 
                    np.array(selected_vectors)
                ).flatten()
                redundancy = np.max(sims)
            
            mmr = mmr_lambda * relevance - (1 - mmr_lambda) * redundancy
            
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx
        
        if best_idx == -1:
            break
        
        # Select this candidate
        selected_ids.append(candidate_ids[best_idx])
        selected_vectors.append(candidate_vectors[best_idx])
        mmr_scores.append(best_mmr)
        remaining_mask[best_idx] = False
    
    # Build result DataFrame in MMR selection order.
    result = candidates_df[candidates_df['film_id'].isin(selected_ids)].copy()
    result = result.set_index('film_id').loc[selected_ids].reset_index()
    result['rank'] = range(1, len(result) + 1)
    result['mmr_score'] = mmr_scores[:len(result)]
    
    return result


# ============================================================================
# MODULE 7 — Explanation Generation
# ============================================================================
def generate_explanation(film_row: pd.Series, user_vector: np.ndarray, 
                         item_matrix: np.ndarray, genre_names: list) -> str:
    """
    Generate human-readable explanation for why a film was recommended.
    """
    film_id = film_row['film_id']
    film_vector = item_matrix[film_id]
    user_vec_flat = user_vector.flatten()
    
    # Element-wise product for contribution analysis
    contributions = film_vector * user_vec_flat
    
    # Genre contributions (first len(genre_names) dimensions)
    n_genres = len(genre_names)
    genre_contribs = contributions[:n_genres]
    
    # Top 2 genres by contribution
    top_genre_indices = np.argsort(genre_contribs)[-2:][::-1]
    top_genres = [
        f"{genre_names[i]} (genre)" 
        for i in top_genre_indices 
        if genre_contribs[i] > 0 and i < len(genre_names)
    ]
    
    # Theme contribution (next 384 dimensions)
    theme_contrib_sum = np.sum(contributions[n_genres:n_genres+384])
    
    explanation_parts = top_genres.copy()
    if theme_contrib_sum > 0.3:
        explanation_parts.append("theme similarity")
    
    if not explanation_parts:
        return "Matched on: general preference alignment"
    
    return f"Matched on: {', '.join(explanation_parts)}"


# ============================================================================
# MODULE 8 — Hybrid Scoring Stub
# ============================================================================
def get_collaborative_score(film_id: int) -> float:
    """
    Stub for future collaborative filtering integration.
    
    TODO: implement when multi-user data is available
    This will query a ratings database and return a CF-based score
    The hybrid blend is: CONFIG["alpha"] * CF_score + (1 - CONFIG["alpha"]) * CB_score
    """
    return 0.0


# ============================================================================
# MODULE 9 — Main Entry Point
# ============================================================================
def recommend(top_n: Optional[int] = None, mmr_lambda: Optional[float] = None) -> pd.DataFrame:
    """
    Main orchestration function for generating recommendations.
    
    Returns DataFrame with columns: rank, Name, Year, genres, themes, 
    similarity_score, mmr_score, explanation
    """
    # Use CONFIG defaults if not overridden
    top_n = top_n if top_n is not None else CONFIG['top_n']
    mmr_lambda = mmr_lambda if mmr_lambda is not None else CONFIG['mmr_lambda']
    
    # 1. Load data
    df = load_data('ratings.csv', 'browser_scraped_themes.json')
    
    # 2. Build feature tracks
    genres_mat, themes_mat, year_mat, genre_names, _ = build_feature_tracks(df)
    
    # 3. Build item matrix (uses cache if available)
    item_matrix = build_item_matrix(genres_mat, themes_mat, year_mat, CONFIG['cache_path'])
    
    # 4. Build user vector
    user_vector = build_user_vector(df, item_matrix)
    
    # 5. Score and filter films
    candidates = score_films(user_vector, item_matrix, df)
    
    # 6. Apply MMR reranking
    ranked = apply_mmr(candidates, item_matrix, top_n, mmr_lambda)
    
    # 7. Generate explanations
    ranked['explanation'] = ranked.apply(
        lambda row: generate_explanation(row, user_vector, item_matrix, genre_names), 
        axis=1
    )
    
    # 8. Format and print results
    _print_results(ranked)
    
    # 9. Return with specified column order
    return ranked[['rank', 'Name', 'Year', 'genres', 'themes', 
                   'similarity_score', 'mmr_score', 'explanation']]


def recommend_similar(title: str, top_n: int = 10) -> pd.DataFrame:
    """
    Generate "more like this" recommendations for a specific film.
    
    Uses the film's item vector as query instead of user vector.
    """
    # Load data and build matrices (same as recommend)
    df = load_data('ratings.csv', 'browser_scraped_themes.json')
    genres_mat, themes_mat, year_mat, genre_names, _ = build_feature_tracks(df)
    item_matrix = build_item_matrix(genres_mat, themes_mat, year_mat, CONFIG['cache_path'])
    
    # Find the query film
    query_row = df[df['Name'].str.lower() == title.lower()]
    if len(query_row) == 0:
        raise ValueError(f"Film not found: '{title}'")
    
    query_id = query_row['film_id'].values[0]
    query_vector = item_matrix[query_id:query_id+1]
    query_vector = normalize(query_vector, norm='l2')
    
    # Score films against query vector (exclude the query film itself)
    scores = cosine_similarity(query_vector, item_matrix).flatten()
    candidates = df.copy()
    candidates['similarity_score'] = scores
    candidates = candidates[candidates['film_id'] != query_id]
    candidates = candidates[candidates['similarity_score'] >= CONFIG['score_floor']]
    candidates = candidates.sort_values('similarity_score', ascending=False)
    
    # Apply MMR
    ranked = apply_mmr(candidates, item_matrix, top_n, CONFIG['mmr_lambda'])
    
    # Generate explanations
    ranked['explanation'] = ranked.apply(
        lambda row: generate_explanation(row, query_vector, item_matrix, genre_names), 
        axis=1
    )
    
    # Print and return
    _print_results(ranked)
    return ranked[['rank', 'Name', 'Year', 'genres', 'themes', 
                   'similarity_score', 'mmr_score', 'explanation']]


def recommend_from_backend(
    rated_movies: Iterable,
    candidate_movies: Iterable,
    top_n: Optional[int] = None,
    mmr_lambda: Optional[float] = None,
) -> list[dict]:
    """
    Generate recommendations from backend ORM objects.

    `rated_movies` should be LoggedMovie rows with ratings. `candidate_movies`
    should be MovieCache rows. The return shape matches the router convention:
    [{"movie": orm_object, "score": 0-100, "reason": "..."}].
    """
    top_n = top_n if top_n is not None else CONFIG['top_n']
    mmr_lambda = mmr_lambda if mmr_lambda is not None else CONFIG['mmr_lambda']

    rated_df = movies_to_dataframe(rated_movies, include_rating=True)
    candidates_df = movies_to_dataframe(candidate_movies, include_rating=False)
    if rated_df.empty or candidates_df.empty:
        return []

    # Keep only usable ratings; neutral/empty rows do not teach the profile.
    rated_df = rated_df[rated_df["Rating"] > 0].copy()
    if rated_df.empty:
        return []

    combined = pd.concat(
        [
            rated_df.assign(_is_candidate=False),
            candidates_df.assign(Rating=np.nan, _is_candidate=True),
        ],
        ignore_index=True,
    )
    combined["film_id"] = range(len(combined))

    genres_mat, themes_mat, year_mat, genre_names, _ = build_feature_tracks(combined)
    item_matrix = build_item_matrix(genres_mat, themes_mat, year_mat, cache_path=None)

    rated_rows = combined[combined["_is_candidate"] == False].copy()
    rated_matrix = item_matrix[rated_rows["film_id"].values]
    user_vector = build_user_vector(rated_rows, rated_matrix)

    scores = cosine_similarity(user_vector, item_matrix).flatten()
    backend_candidates = combined[combined["_is_candidate"] == True].copy()
    backend_candidates["similarity_score"] = scores[backend_candidates["film_id"].values]
    backend_candidates = backend_candidates[
        backend_candidates["similarity_score"] >= CONFIG["score_floor"]
    ].copy()

    if backend_candidates.empty:
        return []

    backend_candidates = backend_candidates.sort_values(
        "similarity_score", ascending=False
    ).reset_index(drop=True)
    ranked = apply_mmr(backend_candidates, item_matrix, top_n, mmr_lambda)
    if ranked.empty:
        return []

    ranked["explanation"] = ranked.apply(
        lambda row: generate_explanation(row, user_vector, item_matrix, genre_names),
        axis=1,
    )

    results = []
    for _, row in ranked.iterrows():
        results.append(
            {
                "movie": row["_movie"],
                "score": float(round(row["similarity_score"] * 100, 2)),
                "reason": row["explanation"],
            }
        )
    return results


def _print_results(df: pd.DataFrame):
    """Helper: print results in readable format."""
    if len(df) == 0:
        print("No recommendations found.")
        return
    
    print("\n" + "="*70)
    print("CINE MATCH RECOMMENDATIONS")
    print("="*70 + "\n")
    
    for _, row in df.iterrows():
        genres_str = ', '.join(row['genres']) if row['genres'] else 'N/A'
        themes_str = ', '.join(row['themes'][:3]) + ('...' if len(row['themes']) > 3 else '') if row['themes'] else 'N/A'
        
        print(f"#{row['rank']:<2} {row['Name']} ({int(row['Year'])})")
        print(f"    Genres : {genres_str}")
        print(f"    Themes : {themes_str}")
        print(f"    Score  : {row['similarity_score']:.3f}  (MMR: {row['mmr_score']:.3f})")
        print(f"    Why    : {row['explanation']}")
        print()


# ============================================================================
# Entry Point
# ============================================================================
if __name__ == "__main__":
    # Demo: run recommendations with top 5
    results = recommend(top_n=5)
    
    # Optional: demonstrate recommend_similar
    # similar = recommend_similar("The Dark Knight", top_n=3)
