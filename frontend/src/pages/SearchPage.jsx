import { useState } from 'react'
import { api } from '../hooks/api'
import MovieCard from '../components/MovieCard'

const HINT_QUERIES = [
  "something like Interstellar",
  "I want to cry",
  "feel-good comedy",
  "mind-bending thriller",
  "I'm feeling nostalgic",
  "epic adventure",
  "dark and atmospheric",
  "something funny to cheer me up",
]

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSearch = async (q) => {
    setQuery(q)
    setLoading(true)
    try {
      const data = await api.search(q, 16)
      setResults(data)
    } catch (err) {
      console.error('Search error:', err)
      setResults({ query: q, results: [] })
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (query.trim()) {
      handleSearch(query)
    }
  }

  const handleHintClick = (hint) => {
    handleSearch(hint)
  }

  return (
    <div className="page">
      <h1>Find a Film</h1>

      <form className="search-form" onSubmit={handleSubmit}>
        <input
          type="text"
          className="search-input"
          placeholder="Type anything... 'something like Interstellar', 'I want to cry', etc."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="search-button">Search</button>
      </form>

      <div className="hint-chips">
        {HINT_QUERIES.map((hint) => (
          <button
            key={hint}
            className="hint-chip"
            onClick={() => handleHintClick(hint)}
          >
            {hint}
          </button>
        ))}
      </div>

      {results && (
        <>
          <p className="search-info">
            Scores (0–100): {results.score_label || 'match likelihood'} for "{results.query}"
            {results.reference_title ? ` — using ${results.reference_title} as reference` : ''}
          </p>

          {results.results.length === 0 ? (
            <>
              <p className="empty-state">No results found for "{results.query}"</p>
              <p className="empty-state-sub">Try a simpler query (title or mood), or use the hint chips above.</p>
            </>
          ) : (
            <div className="movie-grid">
              {results.results.map((movie) => (
                <MovieCard key={movie.tmdb_id} movie={movie} />
              ))}
            </div>
          )}
        </>
      )}

      {loading && <p>Searching...</p>}
    </div>
  )
}
