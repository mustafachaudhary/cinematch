import { useState } from 'react'
import { api } from '../hooks/api'

export default function MovieCard({ movie, onLogged }) {
  const [showModal, setShowModal] = useState(false)
  const [rating, setRating] = useState(3.5)
  const [logging, setLogging] = useState(false)

  const handleLog = async () => {
    setLogging(true)
    try {
      await api.logMovie(movie.tmdb_id, rating)
      setShowModal(false)
      setRating(7)
      onLogged?.()
    } catch (err) {
      console.error('Error logging movie:', err)
    } finally {
      setLogging(false)
    }
  }

  const genreText = movie.genres?.slice(0, 2).join(' · ') || ''
  const yearAndGenres = [movie.release_year, genreText].filter(Boolean).join(' · ')

  return (
    <>
      <div className="movie-card">
        {movie.poster_path && (
          <img src={movie.poster_path} alt={movie.title} className="movie-poster" />
        )}
        {!movie.poster_path && <div className="movie-poster-placeholder">No Image</div>}
        
        {movie.score != null && (
          <div className="score-badge">{movie.score.toFixed(1)}%</div>
        )}
        
        <div className="card-footer">
          <h3 className="card-title">{movie.title}</h3>
          <p className="card-meta">{yearAndGenres}</p>
          {movie.reason && <p className="card-reason">{movie.reason}</p>}
          {movie.mood_context && <p className="card-mood">{movie.mood_context}</p>}
        </div>

        <button className="log-button" onClick={() => setShowModal(true)}>
          + Log Film
        </button>
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{movie.title}</h2>
            <p className="modal-year">{movie.release_year}</p>
            
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
                onClick={() => setShowModal(false)}
                disabled={logging}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleLog}
                disabled={logging}
              >
                {logging ? 'Logging...' : 'Log Film'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
