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
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    movie_name = row['Name'].strip()
                    year = int(row['Year']) if row['Year'] else None
                    rating = float(row['Rating']) if row['Rating'] else None
                    
                    if not rating or rating < 0.5 or rating > 5.0: # Letterboxd ratings can be 0.5
                        print(f"⚠️  Skipping '{movie_name}' (Year: {year}): invalid rating: {rating}")
                        skipped += 1
                        continue

                    # Search TMDB first to get canonical tmdb_id for duplicate check
                    print(f"🔍 Searching TMDB for: '{movie_name}' (Year: {year if year else 'N/A'})")
                    results = tmdb.search_movies(movie_name, year=year, limit=5) # Pass year for better accuracy

                    if not results:
                        print(f"  ✗ TMDB not found for '{movie_name}' (Year: {year if year else 'N/A'}), skipping.")
                        skipped += 1
                        continue
                    
                    # Try to match year if available and multiple results
                    movie_data = results[0] # Default to first result
                    if year and len(results) > 1:
                        for r in results:
                            r_year = r.get('release_date', '')
                            if r_year and r_year.startswith(str(year)):
                                movie_data = r
                                break

                    tmdb_id = movie_data['tmdb_id']
                    
                    # Check if already logged by TMDB ID
                    existing_logged_movie = db.query(LoggedMovie).filter(
                        LoggedMovie.tmdb_id == tmdb_id
                    ).first()
                    if existing_logged_movie:
                        print(f"✓ Already logged: {movie_data['title']} (TMDB ID: {tmdb_id}), skipping.")
                        skipped += 1
                        continue

                    # Check if cached and get embedding
                    embedding_list = None
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
                        # Don't commit yet, wait for LoggedMovie to be added in the same transaction
                    else:
                        # If cached, generate embedding for LoggedMovie (as LoggedMovie stores it directly)
                        embedding_list = nlp.embed_movie(movie_data)


                    # Log the movie with rating
                    logged = LoggedMovie(
                        tmdb_id=tmdb_id,
                        title=movie_data['title'],
                        overview=movie_data['overview'],
                        genres=",".join(movie_data['genres']),
                        poster_path=movie_data['poster_path'],
                        release_year=movie_data['release_year'],
                        rating=rating,
                        embedding=nlp.embedding_to_json(embedding_list) # Use the obtained embedding
                    )
                    db.add(logged)
                    db.commit() # Commit both cached (if new) and logged movie

                    print(f"  ✅ Logged: {movie_data['title']} ({rating}★)")
                    imported += 1

                except Exception as e:
                    # Catch exceptions and log them, incrementing skipped
                    print(f"  ❌ Error importing '{movie_name}': {str(e)}")
                    skipped += 1
    
    finally:
        db.close()
    print(f"\n✅ Import complete: {imported} movies logged, {skipped} skipped.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_letterboxd.py <path_to_ratings.csv>")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    if not Path(csv_file).exists():
        print(f"❌ File not found: {csv_file}")
        sys.exit(1)
    
    import_ratings(csv_file)
