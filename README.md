# CineMatch 🎬

A personalized movie recommendation web app powered by semantic AI and taste profile personalization.

## What It Does

**CineMatch** combines two powerful features:

1. **Semantic Search** — Type anything in plain English ("something like Interstellar", "I want to cry", "funny movie to cheer me up") and get AI-powered movie recommendations ranked by relevance.

2. **Taste Profile Personalization** — Log movies you've seen with ratings (1–10). The app builds a taste profile vector from your ratings and uses it to personalize the home feed and search results. The more you log, the smarter the recommendations get.

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy, SQLite, sentence-transformers
- **Frontend:** React 18, Vite, vanilla CSS (dark cinematic theme)
- **NLP:** `all-MiniLM-L6-v2` embeddings (384-dimensional vectors)
- **Data:** TMDB API (The Movie Database)

## Quick Start

### Prerequisites

- Python 3.8+
- Node.js 18+
- A free TMDB API key (get one at https://www.themoviedb.org/settings/api)

### 1. Backend Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Linux/Mac
# OR: venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

**Configure API Key:**
Edit `backend/.env`:
```
TMDB_API_KEY=your_actual_key_here
DATABASE_URL=sqlite:///./cinematch.db
```

**Seed the Database** (required first time, takes ~5 minutes):
```bash
python seed.py
```

This downloads the ML model (~90MB) and embeds ~400 movies into vectors.

**Start the API:**
```bash
uvicorn main:app --reload
```

The API runs on `http://localhost:8000`. Visit `http://localhost:8000/docs` for interactive docs.

### 2. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

The app runs on `http://localhost:5173`.

## Features

### 🏠 Discover (Home Feed)
- Browse popular movies or get personalized recommendations
- Log 2+ movies to activate your taste profile
- Each movie shows a % score matching your taste

### 🔍 Search (Semantic Search)
- Type natural language queries
- AI finds movies by meaning, not keywords
- Results blend query relevance (75%) + your taste (25%)
- Try: "something like Interstellar", "I want to cry", "mind-bending thriller"

### 📽️ My Films (Logging)
- Search for movies and rate them (1–10 scale, 0.5 step)
- View your complete movie history
- Update or remove ratings anytime
- See when taste profile becomes active (✦)

## How The AI Works

Every movie is converted to a **384-dimensional vector** using `sentence-transformers`. The input text is:

```
{title}. {overview} Genres: {genre1}, {genre2}, ...
```

Your **taste profile** is a weighted average of your logged movie vectors:
- Weight = `(rating - 5.0) / 5.0`
- Rating 10 → weight +1.0 (strongly like)
- Rating 5 → weight 0.0 (neutral)
- Rating 1 → weight -0.8 (strongly dislike)

**Scoring:**
- Home feed: similarity to taste profile → normalize to [0, 100]%
- Search: 75% query similarity + 25% taste similarity

## Project Structure

```
cinematch/
├── backend/
│   ├── models/
│   │   ├── __init__.py
│   │   └── database.py          # SQLAlchemy ORM (MovieCache, LoggedMovie)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── tmdb.py              # TMDB API client
│   │   └── nlp.py               # Embeddings & scoring
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── movies.py            # /movies routes
│   │   └── logs.py              # /logs routes
│   ├── main.py                  # FastAPI app
│   ├── seed.py                  # Populate database
│   ├── requirements.txt
│   └── .env                     # TMDB_API_KEY
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   └── MovieCard.jsx    # Reusable card component
    │   ├── pages/
    │   │   ├── HomeFeed.jsx     # Home page
    │   │   ├── SearchPage.jsx   # Search page
    │   │   └── LogsPage.jsx     # My Films page
    │   ├── hooks/
    │   │   └── api.js           # API client
    │   ├── App.jsx              # Root component
    │   ├── main.jsx
    │   └── index.css            # Dark theme styling
    ├── index.html
    ├── package.json
    └── vite.config.js
```

## API Endpoints

### Movies
- `GET /movies/feed?limit=10` — Personalized/popular feed
- `GET /movies/search?q=...&limit=10` — Semantic search
- `GET /movies/tmdb-search?q=...` — TMDB title search (for logging)
- `GET /movies/{tmdb_id}` — Movie details

### Logs
- `GET /logs/` — All logged movies
- `POST /logs/` — Log a movie with rating
- `DELETE /logs/{tmdb_id}` — Remove a logged movie

## Design

Dark cinematic theme with gold accents, using **DM Serif Display** (headings) and **DM Sans** (body). Responsive grid layout that adapts to mobile. Cards lift on hover with smooth transitions.

## Important Notes

1. **API Key Security:** Never commit your actual API key. Use `.env` and keep it in `.gitignore`.
2. **Seed Script:** Must run before the backend starts. It downloads the embedding model and pre-computes vectors for all movies.
3. **Database:** Uses SQLite stored in `cinematch.db` in the backend directory.
4. **Taste Profile:** Requires ≥ 2 logged movies to activate. Below that, you see popular movies.

## Troubleshooting

**"API error 401" or "Movie not found":**
- Check that `TMDB_API_KEY` is correct in `.env`
- Make sure you got the key from https://www.themoviedb.org/settings/api

**"Port 5173 in use":**
- Another app is using the port. Stop it or change the port in `vite.config.js`

**"Port 8000 in use":**
- Change the port in the uvicorn command: `uvicorn main:app --port 8001 --reload`

**Slow startup:**
- First time: seed.py downloads the model (100MB) — this takes a few minutes
- Subsequent runs only embed new movies

## Development

To rebuild after changes:

**Backend:**
```bash
# Python files are auto-reloaded with --reload
# No rebuild needed
```

**Frontend:**
```bash
# Hot reload is automatic with Vite
# Just save your JSX files
```

## License

This project is open source.

---

**Happy movie hunting! 🍿**
