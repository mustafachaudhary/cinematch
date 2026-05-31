#!/usr/bin/env python3
"""
Import Letterboxd data into CineMatch database.
Reads ratings.csv and watched.csv, enriches with TMDB data.
"""

import csv
import sys
from pathlib import Path
from datetime import datetime
from models.database import SessionLocal, MovieCache, LoggedMovie, init_db
from services import tmdb, nlp

def import_ratings(csv_path: str):
    """Import rated movies from Letterboxd ratings.csv."""
    db = SessionLocal()
    init_db()
    
    imported = 0
    skipped = 0
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                movie_name = row['Name'].strip()
                year = int(row['Year']) if row['Year'] else None
                rating = float(row['Rating']) if row['Rating'] else None
                
                if not rating or rating < 1.0 or rating > 5.0:
                    print(f"⚠️  Skipping {movie_name} - invalid rating: {rating}")
                    skipped += 1
                    continue
                
                # Check if already logged
                existing = db.query(LoggedMovie).filter(
                    LoggedMovie.title == movie_name
                ).first()
                if existing:
                    print(f"✓ Already logged: {movie_name} ({rating}⭐)")
                    skipped += 1
                    continue
                
                # Search TMDB
                print(f"🔍 Searching TMDB for: {movie_name} ({year})")
                results = tmdb.search_movies(movie_name, limit=5)
                
                if not results:
                    print(f"  ✗ Not found on TMDB, skipping")
                    skipped += 1
                    continue
                
                # Try to match year if available
                movie_data = results[0]
                if year and len(results) > 1:
                    for r in results:
                        r_year = r.get('release_date', '')
                        if r_year.startswith(str(year)):
                            movie_data = r
                            break
                
                tmdb_id = movie_data['tmdb_id']
                
                # Check if cached
                cached = db.query(MovieCache).filter(
                    MovieCache.tmdb_id == tmdb_id
                ).first()
                if not cached:
                    embedding_list = nlp.embed_movie(movie_data)
                    cached = MovieCache(
                        tmdb_id=tmdb_id,
                        title=movie_data['title'],
                        overview=movie_data['overview'],
                        genres=",".join(movie_data['genres']),
                        poster_path=movie_data['poster_path'],
                        release_year=movie_data['release_year'],
                        popularity=movie_data['popularity'],
                        vote_average=movie_data['vote_average'],
                        embedding=nlp.embedding_to_json(embedding_list),
                    )
                    db.add(cached)
                    db.commit()
                
                # Log the movie with rating
                logged = LoggedMovie(
                    tmdb_id=tmdb_id,
                    title=movie_data['title'],
                    overview=movie_data['overview'],
                    genres=",".join(movie_data['genres']),
                    poster_path=movie_data['poster_path'],
                    release_year=movie_data['release_year'],
                    rating=rating,
                    embedding=nlp.embedding_to_json(nlp.embed_movie(movie_data))
                )
                db.add(logged)
                db.commit()
                
                print(f"  ✓ Logged: {movie_data['title']} ({rating}⭐)")
                imported += 1
                
            except Exception as e:
                print(f"  ✗ Error importing {movie_name}: {str(e)}")
                skipped += 1
    
    db.close()
    print(f"\n✅ Import complete: {imported} movies logged, {skipped} skipped")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_letterboxd.py <path_to_ratings.csv>")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    if not Path(csv_file).exists():
        print(f"❌ File not found: {csv_file}")
        sys.exit(1)
    
    import_ratings(csv_file)
