"""
Ideas-Based Recommendation Generator
Extracts 10 core ideas from user's taste, uses them to find similar movies.
"""

from collections import Counter, defaultdict
from typing import List, Dict, Set, Tuple
import re

class IdeasModel:
    """
    Generates 10 ideas from user's movie ratings and preferences,
    then uses those ideas as keywords to find similar movies.
    """
    
    def __init__(self, logged_movies: List, num_ideas: int = 10):
        """
        Build ideas from logged movies.
        
        Args:
            logged_movies: List of LoggedMovie ORM objects with ratings
            num_ideas: Number of core ideas to generate (default 10)
        """
        self.num_ideas = num_ideas
        self.high_rated = [m for m in logged_movies if m.rating >= 4.0]  # 4-5 stars
        self.low_rated = [m for m in logged_movies if m.rating <= 2.0]   # 1-2 stars
        
        print(f"\n🧠 Building Ideas Model")
        print(f"   High-rated: {len(self.high_rated)} | Low-rated: {len(self.low_rated)}")
        
        # Extract ideas
        self.ideas = self._generate_ideas()
        self.idea_keywords = self._ideas_to_keywords()
        
        self._print_ideas()
    
    def _generate_ideas(self) -> List[Dict]:
        """
        Extract core ideas from high-rated movies.
        Ideas = combinations of (genre, theme, mood, tone)
        """
        ideas = []
        
        # Collect all attributes from high-rated movies
        all_attrs = defaultdict(int)
        
        for movie in self.high_rated:
            genres = [g.strip() for g in movie.genres.split(",") if g.strip()]
            overview_words = self._extract_keywords(movie.overview)
            
            for genre in genres:
                all_attrs[f"genre:{genre}"] += 1
            
            for word in overview_words:
                all_attrs[f"keyword:{word}"] += 1
        
        # Get top attributes
        top_attrs = sorted(all_attrs.items(), key=lambda x: x[1], reverse=True)[:15]
        
        # Group into ideas
        idea_idx = 0
        for attr_name, count in top_attrs:
            if idea_idx >= self.num_ideas:
                break
            
            ideas.append({
                "id": idea_idx,
                "name": attr_name.replace("genre:", "").replace("keyword:", "").title(),
                "type": "genre" if attr_name.startswith("genre:") else "keyword",
                "value": attr_name.split(":")[1],
                "weight": count
            })
            idea_idx += 1
        
        # Pad with defaults if needed
        while len(ideas) < self.num_ideas:
            ideas.append({
                "id": len(ideas),
                "name": "Hidden Gem",
                "type": "keyword",
                "value": "underrated",
                "weight": 1
            })
        
        return ideas[:self.num_ideas]
    
    def _extract_keywords(self, text: str, top_n: int = 20) -> List[str]:
        """Extract meaningful keywords from text."""
        if not text:
            return []
        
        words = re.findall(r'\b\w+\b', text.lower())
        stop = {
            "a", "an", "and", "are", "as", "for", "i", "in", "is", "it",
            "of", "on", "or", "that", "the", "to", "with", "his", "her",
            "this", "which", "who", "when", "where", "what", "why", "how",
            "he", "she", "they", "them", "but", "been", "be", "have", "has"
        }
        
        filtered = [w for w in words if len(w) > 4 and w not in stop]
        counter = Counter(filtered)
        return [word for word, _ in counter.most_common(top_n)]
    
    def _ideas_to_keywords(self) -> Dict[int, Set[str]]:
        """Convert ideas to searchable keywords."""
        keywords_map = {}
        
        for idea in self.ideas:
            keywords = set()
            
            # Main keyword
            keywords.add(idea["value"])
            
            # Related synonyms based on value
            synonyms = {
                "drama": {"emotion", "character", "relationship", "conflict"},
                "comedy": {"humor", "laugh", "funny", "entertaining"},
                "thriller": {"suspense", "tension", "danger", "mystery"},
                "action": {"adventure", "excitement", "explosive", "fast"},
                "sci-fi": {"future", "technology", "space", "otherworldly"},
                "romance": {"love", "relationship", "passion", "couple"},
                "horror": {"scary", "terror", "supernatural", "dark"},
                "crime": {"detective", "mystery", "criminal", "investigation"},
            }
            
            if idea["value"].lower() in synonyms:
                keywords.update(synonyms[idea["value"].lower()])
            
            keywords_map[idea["id"]] = keywords
        
        return keywords_map
    
    def score_movie_by_ideas(self, candidate_movie) -> Tuple[float, List[str]]:
        """
        Score a movie against the 10 ideas.
        Returns (score 0-100, matched_idea_names)
        """
        score = 30.0  # baseline
        matched_ideas = []
        
        # Extract candidate features
        candidate_genres = {g.strip().lower() for g in candidate_movie.genres.split(",") if g.strip()}
        candidate_keywords = set(self._extract_keywords(candidate_movie.overview))
        candidate_keywords.update(candidate_genres)
        
        # Check against each idea
        ideas_matched = 0
        for idea in self.ideas:
            idea_keywords = self.idea_keywords[idea["id"]]
            
            # Count matches
            matches = len(idea_keywords & candidate_keywords)
            
            if matches > 0:
                score += matches * (idea["weight"] / 2)  # Weight by idea importance
                matched_ideas.append(idea["name"])
                ideas_matched += 1
        
        # Bonus for multiple ideas matching
        if ideas_matched >= 2:
            score += ideas_matched * 5
        
        # Cap at 100
        score = min(100, score)
        
        return score, matched_ideas[:3]  # Return top 3 matched ideas
    
    def _print_ideas(self):
        """Print the 10 core ideas extracted from user's taste."""
        print("\n💡 Your 10 Core Ideas:")
        for idea in self.ideas:
            print(f"   {idea['id']+1}. {idea['name']} (weight: {idea['weight']})")
