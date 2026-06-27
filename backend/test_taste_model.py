#!/usr/bin/env python3
"""
Validation script - test the taste model system end-to-end.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from models.database import SessionLocal, init_db, LoggedMovie, MovieCache
from services.taste_model import TasteProfile, generate_mood_context
from services import tmdb, nlp

def test_taste_model():
    """Test the complete taste model pipeline."""
    
    print("=" * 60)
    print("🎬 CineMatch Taste Model Validation")
    print("=" * 60)
    
    # Initialize database
    print("\n1️⃣  Initializing database...")
    init_db()
    db = SessionLocal()
    
    # Check logged movies
    logged = db.query(LoggedMovie).all()
    print(f"   Found {len(logged)} logged movies")
    
    if len(logged) == 0:
        print("   ⚠️  No logged movies in database")
        print("   💡 Run: python import_letterboxd.py letterboxd_data/ratings.csv")
        db.close()
        return False
    
    # Build taste profile
    print("\n2️⃣  Building taste profile...")
    taste_profile = TasteProfile(logged)
    
    # Get all cached movies
    print("\n3️⃣  Scoring movies...")
    all_movies = db.query(MovieCache).all()
    print(f"   Total movies in cache: {len(all_movies)}")
    
    # Score and rank
    logged_ids = {m.tmdb_id for m in logged}
    scored = []
    
    for movie in all_movies:
        if movie.tmdb_id in logged_ids:
            continue
        
        score, factors = taste_profile.score_movie(movie)
        scored.append({
            "movie": movie,
            "score": score,
            "factors": factors
        })
    
    # Sort by score
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    print(f"   Scored {len(scored)} unwatched movies")
    
    # Display top recommendations
    print("\n4️⃣  Top 10 Recommendations:")
    print("-" * 60)
    
    for i, item in enumerate(scored[:10], 1):
        movie = item["movie"]
        score = item["score"]
        factors = item["factors"]
        mood = generate_mood_context(movie)
        
        factors_str = " + ".join(factors[:2]) if factors else "Matches your taste"
        
        print(f"\n{i}. {movie.title} ({movie.release_year})")
        print(f"   Score: {score:.1f}%")
        print(f"   Reason: {factors_str}")
        print(f"   Mood: {mood}")
        print(f"   Genres: {movie.genres[:50]}...")
    
    print("\n" + "=" * 60)
    print("✅ Taste model validation complete!")
    print("=" * 60)
    
    db.close()
    return True

if __name__ == "__main__":
    success = test_taste_model()
    sys.exit(0 if success else 1)
