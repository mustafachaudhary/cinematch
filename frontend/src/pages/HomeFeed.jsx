import { useState, useEffect } from 'react'
import { api } from '../hooks/api'
import MovieCard from '../components/MovieCard'
import Loading from '../components/Loading'

export default function HomeFeed() {
  const [movies, setMovies] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [logCount, setLogCount] = useState(0)
  const [profileSummary, setProfileSummary] = useState('')

  const isPersonalized = logCount >= 1

  const fetchData = async () => {
    setLoading(true)
    setError(null)
    try {
      const [feedData, logsData, profileData] = await Promise.all([
        api.getFeed(),
        api.getLogs(),
        api.getProfileSummary(),
      ])
      
      // Extract results from feed response
      const movieResults = feedData.results || feedData || []
      setMovies(movieResults)
      setLogCount(logsData.length)
      setProfileSummary(profileData.summary)
    } catch (err) {
      console.error('Error fetching feed:', err)
      setError(err.message || 'Failed to load feed')
      setMovies([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  if (loading) {
    return (
      <div className="page">
        <Loading message="Loading recommendations..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="page">
        <h1>Error</h1>
        <p style={{ color: '#ff6b6b' }}>{error}</p>
        <p>Make sure the backend is running: <code>python -m uvicorn main:app --reload</code></p>
      </div>
    )
  }

  

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>{isPersonalized ? 'Your Feed' : 'Discover Films'}</h1>
        <button 
          onClick={fetchData} 
          style={{ padding: '8px 16px', fontSize: '14px', cursor: 'pointer' }}
        >
          🔄 Refresh
        </button>
      </div>
      
      {isPersonalized ? (
        <p className="subtitle">
          % score shows how likely you are to like each film, based on your ratings.
          {profileSummary ? ` ${profileSummary}` : ''}
        </p>
      ) : (
        <div className="cold-start-banner">
          <p>Log movies for a more personalized feed.</p>
        </div>
      )}

      {movies.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px', color: '#6b6b80' }}>
          <p style={{ fontSize: '16px' }}>No movies available.</p>
          <p style={{ fontSize: '12px' }}>
            Make sure the backend is running and has imported your Letterboxd data.
          </p>
          <button onClick={fetchData} style={{ marginTop: '16px', padding: '8px 16px' }}>
            Try Again
          </button>
        </div>
      ) : (
        <div className="movie-grid">
          {movies.map((movie) => (
            <MovieCard
              key={movie.tmdb_id}
              movie={movie}
              onLogged={fetchData}
            />
          ))}
        </div>
      )}
    </div>
  )
}
