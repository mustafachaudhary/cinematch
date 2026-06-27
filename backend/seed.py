import sys
from models.database import init_db, SessionLocal, MovieCache
from services import tmdb, nlp


def main():
    print("Initializing database...")
    init_db()
    
    db = SessionLocal()
    
    try:
        print("Fetching popular movies...")
        popular = tmdb.fetch_popular_movies(pages=10)
        
        print("Fetching top-rated movies...")
        top_rated = tmdb.fetch_top_rated_movies(pages=10)
        
        # Merge and deduplicate
        movies_dict = {}
        for movie in popular + top_rated:
            movies_dict[movie["tmdb_id"]] = movie
        
        movies = list(movies_dict.values())
        total = len(movies)
        
        print(f"\nTotal unique movies: {total}")
        print("Starting embedding process...\n")
        
        for i, movie in enumerate(movies, 1):
            # Check if already cached
            existing = db.query(MovieCache).filter(MovieCache.tmdb_id == movie["tmdb_id"]).first()
            if existing:
                print(f"[{i}/{total}] Already cached: {movie['title']}")
                continue
            
            # Embed movie
            try:
                embedding = nlp.embed_movie(movie)
                embedding_json = nlp.embedding_to_json(embedding)
                
                # Create cache record
                cache_record = MovieCache(
                    tmdb_id=movie["tmdb_id"],
                    title=movie["title"],
                    overview=movie["overview"],
                    genres=",".join(movie["genres"]),
                    poster_path=movie["poster_path"],
                    release_year=movie["release_year"],
                    popularity=movie["popularity"],
                    vote_average=movie["vote_average"],
                    embedding=embedding_json,
                )
                db.add(cache_record)
                
                # Commit every 50 movies
                if i % 50 == 0:
                    db.commit()
                    print(f"[{i}/{total}] Committed batch")
                else:
                    print(f"[{i}/{total}] Embedding: {movie['title']}")
            except Exception as e:
                print(f"[{i}/{total}] Error embedding {movie['title']}: {e}")
                db.rollback()
                continue
        
        # Final commit
        db.commit()
        print(f"\n✓ Seeding complete! {total} movies cached.")
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
