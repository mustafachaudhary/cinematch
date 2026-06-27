import { useState, useEffect } from 'react'
import { api } from '../hooks/api'
import Loading from '../components/Loading'

export default function LogsPage() {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [tmdbQuery, setTmdbQuery] = useState('')
  const [tmdbResults, setTmdbResults] = useState([])
  const [selectedMovie, setSelectedMovie] = useState(null)
  const [rating, setRating] = useState(7)
  const [logging, setLogging] = useState(false)

  useEffect(() => {
    fetchLogs()
  }, [])

  const fetchLogs = async () => {
    setLoading(true)
    try {
      const data = await api.getLogs()
      setLogs(data)
    } catch (err) {
      console.error('Error fetching logs:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleTmdbSearch = async (q) => {
    setTmdbQuery(q)
    if (!q.trim()) {
      setTmdbResults([])
      return
    }
    try {
      const data = await api.tmdbSearch(q)
      setTmdbResults(data)
    } catch (err) {
      console.error('TMDB search error:', err)
    }
  }

  const handleSelectMovie = (movie) => {
    setSelectedMovie(movie)
    setTmdbQuery(movie.title)
    setTmdbResults([])
    setRating(3.5)
  }

  const handleLogFilm = async () => {
    if (!selectedMovie) return
    setLogging(true)
    try {
      await api.logMovie(selectedMovie.tmdb_id, rating)
      setSelectedMovie(null)
      setTmdbQuery('')
      setRating(7)
      fetchLogs()
    } catch (err) {
      console.error('Error logging film:', err)
    } finally {
      setLogging(false)
    }
  }

  const handleRemoveLog = async (tmdbId) => {
    try {
      await api.removeLog(tmdbId)
      fetchLogs()
    } catch (err) {
      console.error('Error removing log:', err)
    }
  }

  const hasPersonality = logs.length >= 2

  return (
    <div className="page">
      <h1>My Films</h1>

      <section className="log-film-section">
        <h2>Log a Film</h2>
        <input
          type="text"
          className="search-input"
          placeholder="Search for a movie..."
          value={tmdbQuery}
          onChange={(e) => handleTmdbSearch(e.target.value)}
          disabled={selectedMovie ? true : false}
        />
        
        {tmdbResults.length > 0 && (
          <div className="tmdb-dropdown">
            {tmdbResults.map((movie) => (
              <div
                key={movie.tmdb_id}
                className="tmdb-result"
                onClick={() => handleSelectMovie(movie)}
              >
                {movie.poster_path && (
                  <img src={movie.poster_path} alt={movie.title} />
                )}
                <div>
                  <p className="result-title">{movie.title}</p>
                  <p className="result-year">{movie.release_year}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {selectedMovie && (
          <div className="selected-movie-section">
            <h3>{selectedMovie.title}</h3>
            <p>{selectedMovie.release_year}</p>
            
            <div className="rating-section">
              <input
                type="range"
                min="1"
                max="5"
                step="0.5"
                value={rating}
                onChange={(e) => setRating(parseFloat(e.target.value))}
                className="rating-slider"
              />
              <div className="rating-display">{rating.toFixed(1)}</div>
            </div>

            <div className="modal-buttons">
              <button
                className="btn-secondary"
                onClick={() => {
                    setSelectedMovie(null)
                    setTmdbQuery('')
                    setRating(3.5)
                }}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleLogFilm}
                disabled={logging}
              >
                {logging ? 'Logging...' : 'Log Film'}
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="logged-films-section">
        <h2>
          Logged Films
          {hasPersonality && <span className="personality-badge"> ✦</span>}
        </h2>

        {loading ? (
          <Loading message="Loading logged films..." />
        ) : logs.length === 0 ? (
          <p className="empty-state">No films logged yet. Start by logging a film above!</p>
        ) : (
          <div className="movie-grid">
            {logs.map((movie) => (
              <div key={movie.tmdb_id} className="logged-card">
                {movie.poster_path && (
                  <img src={movie.poster_path} alt={movie.title} />
                )}
                {!movie.poster_path && <div className="movie-poster-placeholder">No Image</div>}
                
                <div className="logged-card-footer">
                  <h3>{movie.title}</h3>
                  <p className="logged-rating">{movie.rating.toFixed(1)}/5</p>
                  <p className="card-meta">{movie.release_year}</p>
                </div>

                <button
                  className="remove-button"
                  onClick={() => handleRemoveLog(movie.tmdb_id)}
                  title="Remove log"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
