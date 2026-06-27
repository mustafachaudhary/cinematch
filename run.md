# Running CineMatch

Run these commands from the project root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Backend

```bash
source .venv/bin/activate
python backend/run_server.py
```

Open:

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/docs
```

Useful checks:

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/movies/feed
```

On startup the backend creates the normal app tables plus the collaborative-filtering tables from `backend/schema.sql` via SQLAlchemy models: `films`, `users`, `ratings`, and `user_vectors`.

## Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173 (default) or the Vite server may pick another port such as http://127.0.0.1:5174
```

Quick check the feed from the machine (no browser CORS required):

```bash
curl http://127.0.0.1:8000/movies/feed?limit=4
```

If you're testing from the frontend in a browser and Vite uses a different port, CORS is allowed for localhost ports 5173/5174 and 3000 by default.

## What Is Integrated

`backend/engine/cinematch.py` is now used by the backend feed. When you have logged/rated movies, `/movies/feed` builds a content-based ML profile from those ratings and ranks unwatched cached movies with the CineMatch engine.

The original standalone engine still works with the Letterboxd files:

```bash
cd backend/letterboxd_data
../../.venv/bin/python ../engine/cinematch.py
```

## Reset Local Database

This deletes only the local SQLite database. The backend will recreate tables on the next start.

```bash
rm -f cinematch.db backend/cinematch.db
python backend/run_server.py
```

## Notes

- `pandas` is required for the ML engine and is included in `backend/requirements.txt`.
- The ML engine works without `sentence-transformers`; it falls back to a local scikit-learn vectorizer. Install `sentence-transformers` separately only if you specifically want transformer embeddings.
- Keep using the root `.venv`; the old duplicate `backend/venv` was generated junk.
