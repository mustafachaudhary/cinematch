"""
Advanced Taste Model Service
Analyzes user's movie ratings to create accurate taste profiles.
"""

import numpy as np
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Optional
import re

# Mood/context keywords extracted from movie synopses and genres
MOOD_KEYWORDS = {
    "introspective": {"self-discovery", "identity", "internal conflict", "coming-of-age", "memoir", "psychological"},
    "dark": {"noir", "gritty", "sinister", "twisted", "corruption", "decay", "shadow"},
    "intense": {"thriller", "suspense", "action", "explosive", "dangerous", "high-stakes"},
    "thoughtful": {"philosophical", "existential", "contemplative", "meaning", "question", "explore"},
    "emotional": {"grief", "loss", "love", "heartfelt", "touching", "sacrifice", "redemption"},
    "fun": {"comedy", "adventure", "playful", "witty", "laugh", "joy", "light-hearted"},
    "mind-bending": {"twist", "reality", "illusion", "mystery", "complex", "puzzle", "deception"},
    "visual": {"stunning", "cinematography", "visual", "beautiful", "artistic", "masterpiece"},
    "character-driven": {"character study", "performance", "relationships", "complex characters", "deep"},
    "escapism": {"fantasy", "sci-fi", "adventure", "magical", "epic", "adventure", "otherworldly"},
}

THEME_KEYWORDS = {
    "crime": {"crime", "heist", "detective", "murder", "criminal", "law", "justice", "detective"},
    "sci-fi": {"space", "future", "technology", "alien", "robot", "time", "dimension"},
    "romance": {"love", "relationship", "couple", "romantic", "passion", "marriage"},
    "war": {"war", "military", "soldier", "battle", "conflict", "army"},
    "supernatural": {"ghost", "vampire", "demon", "supernatural", "magic", "paranormal"},
    "coming-of-age": {"teenager", "youth", "school", "growing up", "adolescence"},
    "family": {"family", "parent", "child", "sibling", "generation", "kinship"},
}

def tokenize_synopsis(synopsis: str) -> set:
    """Extract meaningful words from synopsis."""
    if not synopsis:
        return set()
    # Convert to lowercase and split on non-alphanumeric
    words = re.findall(r'\b\w+\b', synopsis.lower())
    # Filter short words and common stopwords
    stop = {"the", "a", "an", "is", "are", "in", "on", "at", "to", "for", "of", "and", "or", "but"}
    return {w for w in words if len(w) > 3 and w not in stop}

def extract_genres(genres_str: str) -> set:
    """Parse comma-separated genres."""
    if not genres_str:
        return set()
    return {g.strip().lower() for g in genres_str.split(",") if g.strip()}

class TasteProfile:
    """Advanced taste profile built from rated movies."""
    
    def __init__(self, logged_movies: List, high_threshold: float = 3.5, low_threshold: float = 2.5):
        """
        Build taste profile from logged movies.
        
        Args:
            logged_movies: List of LoggedMovie ORM objects
            high_threshold: Rating considered "positive signal"
            low_threshold: Rating considered "negative signal"
        """
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        
        # Split into positive/negative groups
        high_rated = [m for m in logged_movies if m.rating >= high_threshold]
        low_rated = [m for m in logged_movies if m.rating <= low_threshold]
        
        print(f"📊 Building taste profile from {len(logged_movies)} ratings")
        print(f"   - High-rated (≥{high_threshold}): {len(high_rated)} movies")
        print(f"   - Low-rated (≤{low_threshold}): {len(low_rated)} movies")
        
        # Extract positive signals
        self.positive_genres = self._extract_genre_distribution(high_rated)
        self.positive_themes = self._extract_theme_distribution(high_rated)
        self.positive_moods = self._extract_mood_distribution(high_rated)
        self.positive_keywords = self._extract_synopsis_keywords(high_rated)
        
        # Extract negative signals (things to avoid)
        self.negative_genres = self._extract_genre_distribution(low_rated) if low_rated else {}
        self.negative_themes = self._extract_theme_distribution(low_rated) if low_rated else {}
        self.negative_moods = self._extract_mood_distribution(low_rated) if low_rated else {}
        self.negative_keywords = self._extract_synopsis_keywords(low_rated) if low_rated else set()
        
        # Compute taste vector (averaged embeddings of high-rated movies)
        self.taste_vector = self._compute_taste_vector(high_rated)
        
        self._print_profile_summary()
    
    def _extract_genre_distribution(self, movies: List) -> Dict[str, float]:
        """Count genre frequency and normalize."""
        counter = Counter()
        for movie in movies:
            genres = extract_genres(movie.genres)
            counter.update(genres)
        
        if not counter:
            return {}
        
        total = sum(counter.values())
        return {g: count / total for g, count in counter.items()}
    
    def _extract_theme_distribution(self, movies: List) -> Dict[str, float]:
        """Extract themes from synopses."""
        theme_scores = defaultdict(int)
        
        for movie in movies:
            if not movie.overview:
                continue
            synopsis_words = tokenize_synopsis(movie.overview)
            
            for theme, keywords in THEME_KEYWORDS.items():
                matches = len(keywords & synopsis_words)
                if matches > 0:
                    theme_scores[theme] += matches
        
        if not theme_scores:
            return {}
        
        total = sum(theme_scores.values())
        return {t: score / total for t, score in theme_scores.items()}
    
    def _extract_mood_distribution(self, movies: List) -> Dict[str, float]:
        """Extract mood/tone from genres and synopsis."""
        mood_scores = defaultdict(int)
        
        for movie in movies:
            genres = extract_genres(movie.genres)
            synopsis = tokenize_synopsis(movie.overview) if movie.overview else set()
            
            for mood, keywords in MOOD_KEYWORDS.items():
                # Match genres
                mood_scores[mood] += len(keywords & genres)
                # Match synopsis
                mood_scores[mood] += len(keywords & synopsis)
        
        if not mood_scores:
            return {}
        
        total = sum(mood_scores.values())
        return {m: score / total for m, score in mood_scores.items()}
    
    def _extract_synopsis_keywords(self, movies: List) -> set:
        """Extract frequently appearing synopsis keywords."""
        all_keywords = []
        for movie in movies:
            if movie.overview:
                all_keywords.extend(tokenize_synopsis(movie.overview))
        
        # Return top keywords
        counter = Counter(all_keywords)
        return {kw for kw, count in counter.most_common(30)}
    
    def _compute_taste_vector(self, movies: List) -> Optional[np.ndarray]:
        """Average embedding vectors of high-rated movies."""
        try:
            from services import nlp
            
            embeddings = []
            for movie in movies:
                if movie.embedding:
                    try:
                        emb = nlp.json_to_embedding(movie.embedding)
                        if emb is not None and len(emb) > 0:
                            embeddings.append(emb)
                    except Exception:
                        pass
            
            if embeddings:
                vector = np.mean(embeddings, axis=0)
                # Normalize
                norm = np.linalg.norm(vector)
                if norm > 0:
                    vector = vector / norm
                return vector
        except Exception as e:
            print(f"⚠️  Could not compute taste vector: {e}")
        
        return None
    
    def score_movie(self, candidate_movie) -> Tuple[float, List[str]]:
        """
        Score a movie against this taste profile.
        Returns (score 0-100, list of matching factors).
        """
        score = 50.0  # baseline
        factors = []
        
        # Extract candidate features
        candidate_genres = extract_genres(candidate_movie.genres)
        candidate_synopsis = tokenize_synopsis(candidate_movie.overview) if candidate_movie.overview else set()
        candidate_themes = self._extract_themes_from_movie(candidate_movie)
        candidate_moods = self._extract_moods_from_movie(candidate_movie)
        
        # Genre matching (positive boost)
        genre_match = sum(self.positive_genres.get(g, 0) for g in candidate_genres)
        if genre_match > 0:
            score += genre_match * 15
            top_genres = sorted(candidate_genres & set(self.positive_genres.keys()), 
                              key=lambda g: self.positive_genres[g], reverse=True)
            factors.append(f"matching genres: {', '.join(top_genres[:2])}")
        
        # Genre penalty (negative)
        genre_penalty = sum(self.negative_genres.get(g, 0) for g in candidate_genres) * 10
        if genre_penalty > 0:
            score -= genre_penalty
            factors.append("contains disliked genres")
        
        # Theme matching
        theme_match = sum(self.positive_themes.get(t, 0) for t in candidate_themes)
        if theme_match > 0:
            score += theme_match * 12
            top_themes = sorted(candidate_themes & set(self.positive_themes.keys()),
                              key=lambda t: self.positive_themes[t], reverse=True)
            factors.append(f"themes: {', '.join(top_themes[:2])}")
        
        # Mood matching
        mood_match = sum(self.positive_moods.get(m, 0) for m in candidate_moods)
        if mood_match > 0:
            score += mood_match * 10
            top_moods = sorted(candidate_moods & set(self.positive_moods.keys()),
                             key=lambda m: self.positive_moods[m], reverse=True)
            factors.append(f"mood: {', '.join(top_moods[:2])}")
        
        # Synopsis keyword matching
        synopsis_match = len(candidate_synopsis & self.positive_keywords)
        if synopsis_match > 0:
            score += synopsis_match * 2
        
        # Embedding similarity (if available)
        if self.taste_vector is not None:
            try:
                from services import nlp
                candidate_emb = nlp.json_to_embedding(candidate_movie.embedding)
                if candidate_emb is not None and len(candidate_emb) > 0:
                    similarity = np.dot(self.taste_vector, candidate_emb)
                    # Normalize similarity to 0-25 point bonus
                    score += max(0, (similarity + 1) / 2 * 25)
            except Exception:
                pass
        
        # Clamp to 0-100
        score = max(0, min(100, score))
        
        return score, factors
    
    def _extract_themes_from_movie(self, movie) -> set:
        """Extract themes present in a movie."""
        synopsis = tokenize_synopsis(movie.overview) if movie.overview else set()
        themes = set()
        
        for theme, keywords in THEME_KEYWORDS.items():
            if keywords & synopsis:
                themes.add(theme)
        
        return themes
    
    def _extract_moods_from_movie(self, movie) -> set:
        """Extract moods/tones from a movie."""
        genres = extract_genres(movie.genres)
        synopsis = tokenize_synopsis(movie.overview) if movie.overview else set()
        moods = set()
        
        for mood, keywords in MOOD_KEYWORDS.items():
            if (keywords & genres) or (keywords & synopsis):
                moods.add(mood)
        
        return moods
    
    def _print_profile_summary(self):
        """Print a summary of the taste profile."""
        print("\n🎬 Your Taste Profile:")
        
        if self.positive_genres:
            top_genres = sorted(self.positive_genres.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"   Favorite genres: {', '.join(g for g, _ in top_genres)}")
        
        if self.positive_themes:
            top_themes = sorted(self.positive_themes.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"   Common themes: {', '.join(t for t, _ in top_themes)}")
        
        if self.positive_moods:
            top_moods = sorted(self.positive_moods.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"   Preferred moods: {', '.join(m for m, _ in top_moods)}")


def generate_mood_context(movie_obj) -> str:
    """
    Generate a mood/context suggestion for watching a movie.
    E.g., "For when you want an intense mind-bending experience"
    """
    genres = extract_genres(movie_obj.genres)
    synopsis = tokenize_synopsis(movie_obj.overview) if movie_obj.overview else set()
    
    detected_moods = []
    
    # Check MOOD_KEYWORDS
    for mood, keywords in MOOD_KEYWORDS.items():
        if (keywords & genres) or (keywords & synopsis):
            detected_moods.append(mood)
    
    # Check THEME_KEYWORDS
    themes = []
    for theme, keywords in THEME_KEYWORDS.items():
        if keywords & synopsis:
            themes.append(theme)
    
    # Build context string
    if not detected_moods:
        return "A great watch"
    
    # Create contextual phrase
    contexts = {
        "introspective": "deep and introspective",
        "dark": "dark and brooding",
        "intense": "intense and gripping",
        "thoughtful": "thoughtful and philosophical",
        "emotional": "moving and emotional",
        "fun": "fun and entertaining",
        "mind-bending": "mind-bending and twisty",
        "visual": "visually stunning",
        "character-driven": "character-focused",
        "escapism": "an escape from reality",
    }
    
    # Pick top 2 moods
    primary = detected_moods[0] if detected_moods else "entertaining"
    secondary = detected_moods[1] if len(detected_moods) > 1 else None
    
    desc = contexts.get(primary, primary)
    if secondary:
        desc += f" and {contexts.get(secondary, secondary)}"
    
    return f"For when you want {desc}"

