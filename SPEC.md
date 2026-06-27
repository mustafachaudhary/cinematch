# CineMatch — Full Project Specification
> Give this entire document to your AI coding agent. Every decision has already been made. Do not deviate.

---

## 0. What This App Is

CineMatch is a personalized movie recommendation web app. It has two core features:

1. **Semantic search** — the user types anything in plain English ("something like Interstellar", "I want to cry", "funny movie to cheer me up") and the app returns ranked movie recommendations with a % score showing how well each film matches the query.

2. **Letterboxd-style logging + taste profile** — the user logs movies they've seen with a rating (1–10). The app builds a taste profile vector from those ratings and uses it to personalize the home feed and blend into search results. The more movies logged, the smarter the recommendations get.

The % score changes meaning based on context:
- Home feed → % match to your taste profile
- Search results → % how well the movie matches your query (blended with taste profile at 25% weight if available)

---

## 1. Folder Structure

```
cinematch/
├── backend/
│   ├── main.py
│   ├── seed.py
│   ├── requirements.txt
│   ├── .env
│   ├── models/
│   │   ├── __init__.py          ← empty file
│   │   └── database.py
│   ├── services/
│   │   ├── __init__.py          ← empty file
│   │   ├── tmdb.py
│   │   └── nlp.py
│   └── routers/
│       ├── __init__.py          ← empty file
│       ├── movies.py
│       └── logs.py
└── frontend/
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── App.jsx
        ├── index.css
        ├── hooks/
        │   └── api.js
        ├── components/
        │   └── MovieCard.jsx
        └── pages/
            ├── HomeFeed.jsx
            ├── SearchPage.jsx
            └── LogsPage.jsx
```

Create every `__init__.py` as an empty file. Without them Python cannot import from subdirectories.

---

## 2. Environment & Dependencies

### Backend — `backend/requirements.txt`

```
fastapi==0.111.0
uvicorn==0.29.0
python-dotenv==1.0.1
requests==2.31.0
sentence-transformers==2.7.0
numpy==1.26.4
scikit-learn==1.4.2
sqlalchemy==2.0.30
aiosqlite==0.20.0
pydantic==2.7.1
```

Install with:
```bash
cd backend
python -m venv venv
source venv/bin/activate        # Linux/Mac
# OR: venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### Backend — `backend/.env`

```
TMDB_API_KEY=YOUR_KEY_HERE
DATABASE_URL=sqlite:///./cinematch.db
```

Never hardcode the API key anywhere in Python files. Always load from `.env` using `python-dotenv`.

### Frontend — `frontend/package.json`

```json
{
  "name": "cinematch-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.4.2"
  }
}
```

### Frontend — `frontend/vite.config.js`

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 }
})
```

---

## 3. Database — `backend/models/database.py`

Use SQLAlchemy with SQLite. Two tables:

### Table 1: `movie_cache`
Stores movies fetched from TMDB, pre-embedded. This is the pool the recommendation engine searches over.

| Column | Type | Notes |
|---|---|---|
| id | Integer | primary key |
| tmdb_id | Integer | unique, indexed |
| title | String | |
| overview | Text | full plot summary |
| genres | String | comma-separated genre names e.g. "Action,Drama" |
| poster_path | String | full URL e.g. `https://image.tmdb.org/t/p/w500/abc.jpg` |
| release_year | Integer | |
| popularity | Float | from TMDB |
| vote_average | Float | from TMDB |
| embedding | Text | JSON-serialized list of floats e.g. `"[0.12, -0.34, ...]"` |
| cached_at | DateTime | default=now |

### Table 2: `logged_movies`
Stores movies the user has logged with their rating.

| Column | Type | Notes |
|---|---|---|
| id | Integer | primary key |
| tmdb_id | Integer | unique, indexed |
| title | String | |
| overview | Text | |
| genres | String | comma-separated |
| poster_path | String | full URL |
| release_year | Integer | |
| rating | Float | user rating 1.0–10.0 |
| embedding | Text | JSON-serialized — copied from cache or freshly generated |
| logged_at | DateTime | default=now |

### Functions to implement:

```python
def get_db():
    # yields a SQLAlchemy session, closes it after

def init_db():
    # creates all tables if they don't exist
    # call this on app startup
```

---

## 4. TMDB Service — `backend/services/tmdb.py`

TMDB API base URL: `https://api.themoviedb.org/3`
Poster image base URL: `https://image.tmdb.org/t/p/w500`

Load `TMDB_API_KEY` from environment via `python-dotenv`. Pass it as `api_key` query parameter on every request.

### Internal helper: `_get(endpoint, params) -> dict`
Makes a GET request to TMDB. Adds `api_key` to params automatically. Raises on non-200.

### Internal helper: `get_genre_map() -> dict`
Calls `/genre/movie/list`. Returns `{genre_id: genre_name}` e.g. `{28: "Action", 18: "Drama"}`.

### Internal helper: `_normalize(movie_dict, genre_map) -> dict | None`
Takes a raw TMDB movie dict and returns a clean normalized dict. Returns `None` if the movie has no overview or no title (skip these — they're useless for NLP).

Output format:
```python
{
    "tmdb_id": int,
    "title": str,
    "overview": str,
    "genres": list[str],          # e.g. ["Action", "Drama"]
    "poster_path": str | None,    # full URL or None
    "release_year": int | None,
    "popularity": float,
    "vote_average": float,
}
```

For `poster_path`: if the raw TMDB value is e.g. `/abc.jpg`, return `https://image.tmdb.org/t/p/w500/abc.jpg`. If it's None, return None.

For `release_year`: parse from `release_date` field (format `"YYYY-MM-DD"`). Take the first 4 chars and cast to int. If missing or malformed, return None.

For `genres`: look up each id in `genre_ids` field using `genre_map`. Skip any id not in the map.

### Public functions:

**`fetch_popular_movies(pages=10) -> list[dict]`**
Calls `/movie/popular` for pages 1 through `pages`. Normalizes each result. Returns flat list of all normalized movies.

**`fetch_top_rated_movies(pages=10) -> list[dict]`**
Same but calls `/movie/top_rated`.

**`search_movies(query, limit=10) -> list[dict]`**
Calls `/search/movie` with `{"query": query, "language": "en-US"}`. Normalizes and returns up to `limit` results.

**`get_movie_details(tmdb_id) -> dict | None`**
Calls `/movie/{tmdb_id}`. The details endpoint returns `genres` as a list of objects `[{"id": 28, "name": "Action"}]` instead of `genre_ids`. Before calling `_normalize`, convert it: `m["genre_ids"] = [g["id"] for g in m.get("genres", [])]`. Returns None on any exception.

**`get_similar_movies(tmdb_id, limit=20) -> list[dict]`**
Calls `/movie/{tmdb_id}/similar`. Returns up to `limit` normalized results.

---

## 5. NLP Service — `backend/services/nlp.py`

This is the core intelligence layer. Uses `sentence-transformers`.

### Model
```python
MODEL_NAME = "all-MiniLM-L6-v2"
```
Load it lazily (only on first use). Store in a module-level `_model` variable. This avoids loading it at import time.

```python
_model = None

def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model
```

### `embed_text(text: str) -> list[float]`
Embeds a string. Use `normalize_embeddings=True` so all vectors are unit length (required for cosine similarity to work correctly). Returns Python list of floats.

### `embed_movie(movie: dict) -> list[float]`
Builds a rich text representation of a movie before embedding:
```python
text = f"{movie['title']}. {movie['overview']} Genres: {', '.join(movie.get('genres', []))}."
return embed_text(text)
```
Including genres in the text makes the embedding capture genre information, which significantly improves recommendations.

### `embedding_to_json(embedding: list[float]) -> str`
Serializes an embedding to a JSON string for storage in SQLite.

### `json_to_embedding(s: str) -> np.ndarray`
Deserializes a JSON string back to a float32 numpy array.

### `build_taste_profile(logged_movies: list) -> np.ndarray | None`
This is the personalization engine. Returns None if no logged movies have embeddings.

Algorithm:
1. For each logged movie, get its embedding and rating.
2. Compute `weight = (rating - 5.0) / 5.0`. This maps:
   - Rating 10 → weight +1.0 (strong like)
   - Rating 5 → weight 0.0 (neutral)
   - Rating 1 → weight -0.8 (strong dislike)
3. Add `weight * embedding` to a running sum.
4. Track total absolute weight.
5. Divide the sum by total weight to get the profile vector.
6. Normalize the profile to unit length.

This means movies rated above 5 pull the profile toward their embedding, and movies rated below 5 push the profile away. The profile literally points in the direction of the user's preferred movie-space.

Return None if all weights are zero or no embeddings exist.

### `score_movies_by_query(query_embedding, candidate_movies, taste_profile=None, query_weight=0.7, taste_weight=0.3) -> list[dict]`

Scores every candidate movie against the query. Optionally blends taste profile.

For each movie:
1. Get its embedding from `movie.embedding` (stored as JSON string → convert to ndarray).
2. Compute `query_sim = cosine_similarity(query_vec, movie_vec)` → scalar in [-1, 1].
3. If taste_profile is provided:
   - Compute `taste_sim = cosine_similarity(taste_profile, movie_vec)` → scalar.
   - Normalize both to [0, 1]: `x_norm = (x + 1) / 2`
   - Final score: `query_weight * query_sim_norm + taste_weight * taste_sim_norm`
4. If no taste profile: `score = (query_sim + 1) / 2`
5. Multiply by 100 and round to 1 decimal → percentage.

Return list of `{"movie": movie_orm_object, "score": float}`, sorted descending by score.

### `score_movies_by_taste(taste_profile, candidate_movies) -> list[dict]`

Used for the home feed. Scores purely by taste profile match.

For each movie: `sim = cosine_similarity(taste_profile, movie_vec)` → normalize to [0,1] → multiply by 100.

Return sorted descending by score.

---

## 6. API Routes

### `backend/main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from models.database import init_db
from routers import movies, logs

app = FastAPI(title="CineMatch API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

app.include_router(movies.router)
app.include_router(logs.router)

@app.get("/")
def root():
    return {"status": "CineMatch API running"}
```

Run with: `uvicorn main:app --reload`

---

### `backend/routers/movies.py`

Prefix: `/movies`

#### `GET /movies/feed?limit=10`
Returns the home feed.

Logic:
1. Load all `LoggedMovie` records from DB.
2. Build taste profile from them using `nlp.build_taste_profile()`.
3. Load all `MovieCache` records.
4. Filter out any movies the user has already logged (by `tmdb_id`).
5. If taste profile exists AND user has logged ≥ 2 movies:
   - Score using `nlp.score_movies_by_taste()`.
   - Return top `limit` with scores.
6. Else (cold start):
   - Sort by `vote_average * popularity` descending.
   - Return top `limit` WITHOUT scores (score=None).

Response format per movie:
```json
{
  "tmdb_id": 157336,
  "title": "Interstellar",
  "overview": "...",
  "genres": ["Adventure", "Drama", "Science Fiction"],
  "poster_path": "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIe.jpg",
  "release_year": 2014,
  "vote_average": 8.4,
  "score": 87.3
}
```

#### `GET /movies/search?q=...&limit=10`
Semantic search endpoint.

Logic:
1. Embed the query string using `nlp.embed_text(q)`.
2. Load all logged movies, build taste profile.
3. Load all `MovieCache` records.
4. Call `nlp.score_movies_by_query()` with:
   - `query_weight=0.75, taste_weight=0.25` if taste profile exists and ≥ 2 logs
   - `taste_profile=None` otherwise
5. Return top `limit`.

Response:
```json
{
  "query": "something like Interstellar",
  "results": [{ ...movie fields + score }, ...]
}
```

#### `GET /movies/tmdb-search?q=...`
Searches TMDB by title (for the "log a movie" flow — user types a movie name to find it).

Logic: Call `tmdb.search_movies(q, limit=8)`. Return raw list. These do NOT have scores. They are just for the user to pick which movie to log.

#### `GET /movies/{tmdb_id}`
Returns details for a single movie.

Logic: Check `MovieCache` first. If found, return it. If not, call `tmdb.get_movie_details(tmdb_id)`.

---

### `backend/routers/logs.py`

Prefix: `/logs`

#### `GET /logs/`
Returns all logged movies, ordered by `logged_at` descending.

Response: list of:
```json
{
  "id": 1,
  "tmdb_id": 157336,
  "title": "Interstellar",
  "poster_path": "...",
  "genres": ["Adventure", "Drama"],
  "release_year": 2014,
  "rating": 9.0,
  "logged_at": "2024-01-15T10:30:00"
}
```

#### `POST /logs/`
Logs a movie with a rating.

Request body:
```json
{ "tmdb_id": 157336, "rating": 9.0 }
```

Validation: rating must be between 1 and 10 inclusive. Return 400 if not.

Logic:
1. Check if movie is already logged. If yes, just update the rating and return.
2. Check `MovieCache` for the movie. If cached, use its data and embedding.
3. If not cached, call `tmdb.get_movie_details(tmdb_id)`. If not found, return 404.
4. Generate embedding using `nlp.embed_movie(movie_data)`.
5. Create `LoggedMovie` record and save.

Return the logged movie in the same format as `GET /logs/`.

#### `DELETE /logs/{tmdb_id}`
Removes a logged movie by its TMDB ID. Returns 404 if not found.

Response: `{"deleted": tmdb_id}`

---

## 7. Seed Script — `backend/seed.py`

This script populates the `movie_cache` table. Run it once before starting the app.

```
python seed.py
```

Steps:
1. Call `init_db()` to create tables.
2. Fetch popular movies (10 pages = ~200 movies).
3. Fetch top-rated movies (10 pages = ~200 movies).
4. Merge into a dict keyed by `tmdb_id` to deduplicate.
5. For each unique movie:
   - Skip if already in `MovieCache`.
   - Generate embedding using `nlp.embed_movie()`.
   - Save to `MovieCache`.
   - Commit every 50 movies (don't commit one at a time — it's slow).
6. Print progress as it goes: `[i/total] Embedding: {title}`

This takes about 5 minutes on first run because it needs to download the embedding model (~90MB) and embed ~400 movies. Subsequent runs skip already-cached movies.

---

## 8. Frontend

### `frontend/src/hooks/api.js`

All API calls go through this file. Base URL is `http://localhost:8000`.

```javascript
const BASE = "http://localhost:8000"

export async function apiFetch(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export const api = {
  getFeed: (limit = 10) => apiFetch(`/movies/feed?limit=${limit}`),
  search: (q) => apiFetch(`/movies/search?q=${encodeURIComponent(q)}`),
  tmdbSearch: (q) => apiFetch(`/movies/tmdb-search?q=${encodeURIComponent(q)}`),
  getLogs: () => apiFetch("/logs/"),
  logMovie: (tmdb_id, rating) =>
    apiFetch("/logs/", { method: "POST", body: JSON.stringify({ tmdb_id, rating }) }),
  removeLog: (tmdb_id) =>
    apiFetch(`/logs/${tmdb_id}`, { method: "DELETE" }),
}
```

---

### `frontend/src/App.jsx`

Top-level component. Manages which page is shown.

State: `page` (string) — one of `"home"`, `"search"`, `"logs"`.

Renders:
- A sticky navbar with the app name "CineMatch" and three nav buttons: "Discover", "Search", "My Films"
- The current page component below the navbar

```jsx
import { useState } from "react"
import HomeFeed from "./pages/HomeFeed"
import SearchPage from "./pages/SearchPage"
import LogsPage from "./pages/LogsPage"
import "./index.css"

export default function App() {
  const [page, setPage] = useState("home")
  return (
    <div className="app">
      <nav className="navbar">
        {/* brand + nav buttons */}
      </nav>
      <main className="main-content">
        {page === "home" && <HomeFeed />}
        {page === "search" && <SearchPage />}
        {page === "logs" && <LogsPage />}
      </main>
    </div>
  )
}
```

---

### `frontend/src/components/MovieCard.jsx`

Reusable card component for displaying a movie.

Props:
- `movie` — movie object with `tmdb_id`, `title`, `poster_path`, `genres`, `release_year`, `score` (nullable)
- `onLogged` — callback called after the user successfully logs the movie

Visual structure:
- Movie poster image (2:3 aspect ratio). If no poster_path, show a placeholder.
- Score badge in top-right corner, only if `movie.score != null`. Shows e.g. `87.3%`
- Card footer with title and `{release_year} · {genre1} · {genre2}` (max 2 genres)
- On hover: a "+ Log Film" button appears overlaid at the bottom of the card

Clicking "+ Log Film" opens a modal:
- Shows movie title and year
- A number input or slider for rating (1–10, step 0.5, default 7)
- Shows the current rating value as a large number
- "Cancel" button closes modal
- "Log Film" button calls `api.logMovie(movie.tmdb_id, rating)`, then calls `onLogged()`, then closes modal
- Show "Logging..." on the button while the request is in flight

---

### `frontend/src/pages/HomeFeed.jsx`

State:
- `movies` — array of movie objects from `/movies/feed`
- `loading` — boolean
- `logCount` — number of logged movies (from `/logs/`)

On mount: fetch both `/movies/feed` and `/logs/` in parallel using `Promise.all`.

`isPersonalized = logCount >= 2`

Renders:
- Page title: "Your Feed" if personalized, "Discover Films" if not
- Subtitle explaining what the % score means, or a tip to log more movies if < 2 logged
- If < 2 logged: show a banner explaining how to get personalized recommendations
- Movie grid using `MovieCard` components
- When `onLogged` fires on any card, refetch both feed and logs

---

### `frontend/src/pages/SearchPage.jsx`

State:
- `query` — string
- `results` — null (initial) or the API response object
- `loading` — boolean

Renders:
- Page title "Find a Film"
- Search bar with a submit button (also submits on Enter key)
- A row of clickable hint chips with example queries:
  - "something like Interstellar"
  - "I want to cry"
  - "feel-good comedy"
  - "mind-bending thriller"
  - "I'm feeling nostalgic"
  - "epic adventure"
  - "dark and atmospheric"
  - "something funny to cheer me up"
  - Clicking a hint sets the query AND immediately triggers search
- After search: a banner explaining the scores ("% score shows how well each film matches your query")
- Results grid using `MovieCard` components
- If no results: empty state message

---

### `frontend/src/pages/LogsPage.jsx`

State:
- `logs` — array of logged movies
- `loading` — boolean
- `tmdbQuery` — string for the "find movie to log" search input
- `tmdbResults` — array of TMDB search results (dropdown)
- `selectedMovie` — the movie the user picked from the dropdown
- `rating` — number, default 7
- `logging` — boolean

On mount: fetch `/logs/`.

**Top section — "Log a Film":**

A search input. As the user types (debounced 400ms), calls `/movies/tmdb-search?q=...` and shows a dropdown of results below the input. Each result shows the poster thumbnail, title, and year.

When user clicks a result:
- Populate the input with the movie title
- Hide the dropdown
- Show a rating UI (slider 1–10, step 0.5, showing current value)
- Show "Log Film" and "Cancel" buttons

On "Log Film": call `api.logMovie(selectedMovie.tmdb_id, rating)`, clear the form, reload logs.

**Bottom section — "Logged Films":**

Grid of logged movies. Each card shows:
- Poster
- Title
- Rating (e.g. `9/10`)
- Year
- A remove button (×) that appears on hover, calls `api.removeLog(tmdb_id)`

Show "taste profile active ✦" next to the section title when `logs.length >= 2`.

---

## 9. Styling — `frontend/src/index.css`

Dark cinematic theme. Key design decisions:

**Colors (CSS variables):**
```css
:root {
  --bg: #0a0a0f;         /* near-black background */
  --bg2: #111118;        /* card background */
  --bg3: #1a1a24;        /* input background */
  --border: rgba(255,255,255,0.07);
  --text: #e8e8f0;
  --text-muted: #6b6b80;
  --text-dim: #9090a8;
  --accent: #e8c87a;     /* gold — primary accent */
  --accent2: #c47c5a;    /* warm orange — secondary */
}
```

**Typography:**
- Display/headings: `DM Serif Display` (Google Fonts)
- Body: `DM Sans` (Google Fonts)
- Import both from Google Fonts at the top of the CSS file

**Layout:**
- Sticky navbar, 60px height, blurred backdrop
- Max content width: 1300px, centered, padding 2.5rem
- Movie grid: `grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))`, gap 1.25rem

**Movie card:**
- Poster: `aspect-ratio: 2/3`, covers the card
- On hover: card lifts (`translateY(-4px)`), box shadow deepens, "Log Film" button fades in at bottom
- Score badge: top-right corner, dark background, gold text, small border

**Accent button (Log Film, primary actions):** gold background (`--accent`), dark text
**Secondary button (Cancel):** dark background, muted text

**Search input:** dark background, subtle border that glows gold on focus

**Hint chips:** pill-shaped, subtle border, border turns gold on hover

---

## 10. Running the App

### Terminal 1 — Backend:
```bash
cd cinematch/backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python seed.py           # REQUIRED first time — takes ~5 min
uvicorn main:app --reload
```

### Terminal 2 — Frontend:
```bash
cd cinematch/frontend
npm install
npm run dev
```

Open: `http://localhost:5173`

API docs (auto-generated): `http://localhost:8000/docs`

---

## 11. Rules — Do Not Violate These

1. **Never hardcode the TMDB API key.** Always load from `.env` via `python-dotenv`.
2. **Always create `__init__.py` files** in `models/`, `services/`, `routers/`. Without these, Python imports fail.
3. **The NLP model loads lazily.** Do not instantiate `SentenceTransformer` at module import time. It takes several seconds and will slow down startup.
4. **Embeddings are stored as JSON strings in SQLite.** Always convert with `json.dumps()` before saving and `json.loads()` + `np.array(..., dtype=np.float32)` when loading.
5. **Always use `normalize_embeddings=True`** when calling `model.encode()`. Without normalization, cosine similarity scores are incorrect.
6. **CORS must be configured.** The React app runs on port 5173. The FastAPI backend must allow requests from that origin.
7. **Never use `localStorage` in the React frontend.** All state lives in React component state and is fetched from the API.
8. **The seed script must be run before starting the backend** for the first time. Without it, the movie cache is empty and the app shows nothing.
9. **Genres are stored as comma-separated strings** in SQLite (`"Action,Drama"`). Always split on `","` when reading, join with `","` when writing. Return as a list in API responses.
10. **The taste profile requires ≥ 2 logged movies** to be used. Below that, show cold-start content (popular movies, no scores).
11. **Do not use any state management library** (Redux, Zustand, etc.). Plain React `useState` and `useEffect` is sufficient.
12. **Do not use any CSS framework** (Tailwind, Bootstrap, etc.). Write plain CSS with the variables defined above.
13. **No TypeScript.** Plain JavaScript and JSX only.
14. **All API calls go through `src/hooks/api.js`.** Never write `fetch()` calls directly in components.

---

## 12. How the NLP Works (For Context)

The entire recommendation system is built on **semantic vector embeddings**.

Every movie is converted to a 384-dimensional vector using `sentence-transformers/all-MiniLM-L6-v2`. The input text is `"{title}. {overview} Genres: {genres}."`. This vector captures the semantic meaning of the movie — its themes, tone, setting, and genre.

When a user types "something like Interstellar", that query is also embedded into a 384-dimensional vector. Movies whose vectors are closest to the query vector (by cosine similarity) are most similar to the query.

The taste profile is a weighted average of the user's logged movie vectors, where the weight is `(rating - 5) / 5`. Movies rated 10 contribute strongly in their direction; movies rated 1 push the profile away. The resulting vector represents "what the user likes" in movie-space.

For the home feed, movies are ranked by how close they are to the taste profile vector.
For search, movies are ranked by a blend: 75% query similarity + 25% taste profile similarity.

The % scores are just the cosine similarity normalized from [-1, 1] to [0, 100].

This is real NLP — not keyword matching, not genre filters. A query like "I'm feeling sad and want something uplifting" will find movies with emotionally warm themes even if those words never appear in the movie descriptions.
