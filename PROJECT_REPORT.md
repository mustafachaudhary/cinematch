# CineMatch Project Report

## Overview

CineMatch is a personalized movie recommendation app that combines semantic NLP embeddings with user taste profile personalization. The backend is built with FastAPI and SQLite, while the frontend uses React and Vite.

The core value proposition is:
- natural-language movie search powered by semantic similarity
- a taste profile based on user-rated films
- content-based recommendations for unwatched movies

## NLP / ML Stack

### Main libraries
- `numpy` — numerical arrays, vector math
- `pandas` — data loading and tabular preprocessing in the ML engine
- `scikit-learn` — feature vectorization, normalization, cosine similarity
- `sentence-transformers` — semantic text embeddings via `all-MiniLM-L6-v2`
- `sqlalchemy` — ORM access to SQLite storage

### Key backend files
- `backend/services/nlp.py` — core NLP + recommendation logic
- `backend/engine/cinematch.py` — content-based recommendation engine, alternative local ML pipeline
- `backend/import_letterboxd.py` — imports user ratings and generates or reuses movie embeddings
- `backend/test_taste_model.py` — validation of the taste profile scoring pipeline
- `backend/routers/movies.py` — search and ranking endpoint orchestration

## Data and Model

### Data sources
- TMDB API for movie metadata
- Letterboxd data import for user ratings and watched history
- SQLite cache in `MovieCache` for movies and stored embeddings

### Movie embedding strategy
- Uses `sentence-transformers/all-MiniLM-L6-v2`
- Input text for each movie:
  - title
  - overview
  - genres
- Embeddings are normalized and stored as JSON strings in SQLite
- Movie vectors are 384-dimensional

### Taste profile
- Built from logged movies with available embeddings
- Each rated movie is converted to a normalized embedding vector
- Weights are derived from rating values, so higher ratings have stronger influence
- The user profile is the weighted average of these vectors, normalized to unit length

## Recommendation and Search Pipeline

### Candidate selection and caching
- The app caches movie data in `MovieCache`
- On search, it loads cached movies and optionally hydrates missing intent-seed titles
- If a title match is found, it anchors similarity search around that reference movie

### Hybrid ranking pipeline

The main search ranking flow is implemented in `backend/services/nlp.py` and orchestrated by `backend/routers/movies.py`.

It is a two-stage pipeline with a 40/40/20 weighting:

1. Feature extraction
   - tokenize the query
   - infer intent from user query tokens
   - detect whether the query is an explicit title search or a mood-style request
   - compute a transformer embedding for the query when available

2. Hybrid scoring
   - semantic similarity (up to 40 points)
     - cosine similarity between query embedding and movie embeddings
     - only if transformer embeddings are available
   - lexical/title matching (up to 40 points)
     - exact title match
     - phrase/title partial match
     - token overlap and genre matches
   - curated mood / intent boost (up to 20 points)
     - mood scores for intents like `cry`, `funny`, `romantic`, `space`
     - genre-based boosts, term matches, and curated title boosts

3. Taste personalization
   - if the user has logged movies, the search result score is also nudged by the taste profile
   - taste profile similarity contributes a personalized relevance signal in query scoring

4. Fallbacks
   - if no ranking results are available, it falls back to TMDB search
   - if the query is clearly a known title, it returns that title first with a strong score

## Pitching Pipeline Explanation

In this project, the "pitching pipeline" refers to the search and recommendation ranking pipeline used to transform a user query into ranked movie recommendations.

### What it does
- converts natural-language queries into semantic and lexical signals
- combines them with mood/intention heuristics
- merges those signals with personal taste profile preferences
- produces an ordered list of recommended movies with explanation metadata

### Why it matters
- it avoids purely keyword-based search
- it captures user intent like "something uplifting" or "sad drama"
- it still honors exact title requests and known movie anchors
- it personalizes results for logged users instead of returning generic hits

### Pipeline stages

1. **Query analysis**
   - tokenize query text
   - infer the search intent
   - detect explicit reference/title similarity
   - compute query embedding if the transformer model is available

2. **Candidate scoring**
   - if the query is a reference movie, score by similarity to that movie
   - otherwise, score each candidate movie using three signals:
     - semantic embedding similarity
     - lexical/text matching
     - mood / curated intent scoring

3. **Taste profile merge**
   - build a user taste vector from rated movies
   - use cosine similarity between taste profile and movie vectors
   - blend taste similarity into search results so personal preference influences ranking

4. **Result construction**
   - place exact title matches first when appropriate
   - remove duplicates
   - format the final recommendation list with score labels and reason text

## Notes and Observations

- The project uses a mix of learned embeddings and rule-based scoring.
- `backend/requirements.txt` lists core ML libraries, but `sentence-transformers` is optional and loaded only if installed.
- There is a strong emphasis on explainable recommendations through reason text and mood/intention labels.

## Suggested next steps

- add `sentence-transformers` to `backend/requirements.txt` if the project should always require embedding support
- document the exact fallback behavior for cold-start users
- consider a small section in the report about data quality for Letterboxd import and embedding cache
