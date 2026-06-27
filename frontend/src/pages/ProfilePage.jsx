import { useState, useEffect } from 'react'
import { api } from '../hooks/api'
import './ProfilePage.css'
import Loading from '../components/Loading'

export default function ProfilePage() {
  const [profile, setProfile] = useState(null)
  const [movies, setMovies] = useState([])
  const [likedMovies, setLikedMovies] = useState([])
  const [loading, setLoading] = useState(true)
  const [showFavoritePicker, setShowFavoritePicker] = useState(null)
  const [favoriteSearch, setFavoriteSearch] = useState('')
  const [ratingDistribution, setRatingDistribution] = useState({})
  const [showCounts, setShowCounts] = useState(true)
  const [importing, setImporting] = useState(false)
  const [showHeartedPage, setShowHeartedPage] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDisplayName, setEditDisplayName] = useState('')
  const [editBio, setEditBio] = useState('')

  useEffect(() => {
    loadProfileData()
  }, [])

  const loadProfileData = async () => {
    setLoading(true)
    try {
      const [profileData, moviesData] = await Promise.all([
        api.getProfile(),
        api.getLogs(),
      ])

      setProfile(profileData)
      setMovies(moviesData)

      // Calculate rating distribution
      const dist = {}
      for (let i = 1; i <= 10; i++) {
        dist[i] = moviesData.filter(m => Math.round(m.rating * 2) === i).length
      }
      setRatingDistribution(dist)

      // Get liked movies (4.5+ stars)
      const liked = moviesData.filter(m => m.rating >= 4.5).sort((a, b) => b.rating - a.rating)
      setLikedMovies(liked)
    } catch (err) {
      console.error('Error loading profile:', err)
      if (err.message?.includes('API error 404')) {
        const fallbackUser = 'mustafa'
        localStorage.setItem('cinematch_username', fallbackUser)
        try {
          const [profileData, moviesData] = await Promise.all([
            api.getProfile(),
            api.getLogs(),
          ])
          setProfile(profileData)
          setMovies(moviesData)

          const dist = {}
          for (let i = 1; i <= 10; i++) {
            dist[i] = moviesData.filter(m => Math.round(m.rating * 2) === i).length
          }
          setRatingDistribution(dist)
          const liked = moviesData.filter(m => m.rating >= 4.5).sort((a, b) => b.rating - a.rating)
          setLikedMovies(liked)
          return
        } catch (innerErr) {
          console.error('Fallback profile load failed:', innerErr)
        }
      }
    } finally {
      setLoading(false)
    }
  }

  const handleSetFavorite = (slot, movieId) => {
    // Update profile
    const updatedProfile = { ...profile }
    updatedProfile[`favorite_movie_${slot + 1}`] = movieId
    api.updateProfile(updatedProfile)
    setProfile(updatedProfile)
    setShowFavoritePicker(null)
    setFavoriteSearch('')
  }

  const handleEditToggle = () => {
    setEditName(profile.username || '')
    setEditDisplayName(profile.display_name || '')
    setEditBio(profile.bio || '')
    setEditing(!editing)
  }

  const handleSaveProfile = async () => {
    try {
      await api.updateProfile({ username: editName, display_name: editDisplayName, bio: editBio })
      await loadProfileData()
      setEditing(false)
      alert('✅ Profile updated')
    } catch (err) {
      console.error('Failed to update profile', err)
      alert('❌ Failed to save')
    }
  }

  const handleSignOut = async () => {
    try {
      const res = await fetch('http://localhost:8000/profile/signout', { method: 'POST' })
      if (!res.ok) throw new Error('Sign out failed')
      await loadProfileData()
      alert('Signed out')
    } catch (err) {
      console.error('Sign out error', err)
      alert('❌ Sign out failed')
    }
  }

  const handlePfpUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    const formData = new FormData()
    formData.append('file', file)

    try {
      const result = await fetch('http://localhost:8000/profile/upload-pfp', {
        method: 'POST',
        body: formData,
      })
      if (!result.ok) {
        const error = await result.json()
        alert(`Error: ${error.detail}`)
        return
      }
      const data = await result.json()
      // Force cache bust with timestamp
      const pfpUrl = `http://localhost:8000${data.pfp_path}?t=${Date.now()}`
      setProfile({ ...profile, pfp_path: pfpUrl })
      alert('✅ Profile picture updated!')
    } catch (err) {
      console.error('Error uploading profile picture:', err)
      alert('❌ Upload failed')
    }
  }

  const handleLetterboxdImport = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    setImporting(true)
    const formData = new FormData()
    formData.append('file', file)

    try {
      const result = await fetch('http://localhost:8000/profile/import-letterboxd', {
        method: 'POST',
        body: formData,
      })
      const data = await result.json()
      alert(`✅ Imported ${data.imported} movies! (${data.skipped} duplicates/invalid)`)
      loadProfileData()
    } catch (err) {
      console.error('Error importing Letterboxd:', err)
      alert('❌ Import failed')
    } finally {
      setImporting(false)
    }
  }

  const getPfpUrl = () => {
    if (!profile.pfp_path) return null
    // If already a full URL, return as is
    if (profile.pfp_path.startsWith('http')) return profile.pfp_path
    // Otherwise prepend server URL
    return `http://localhost:8000${profile.pfp_path}?t=${Date.now()}`
  }

  if (loading) {
    return (
      <div className="page">
        <Loading message="Loading profile..." />
      </div>
    )
  }

  if (!profile) {
    return <div className="page"><p>Profile not found</p></div>
  }

  if (showHeartedPage) {
    return <HeartedMoviesPage onBack={() => setShowHeartedPage(false)} movies={likedMovies} />
  }

  const maxCount = Math.max(...Object.values(ratingDistribution))
  const ratedMovies = movies.length
  const totalMovies = profile.stats?.watched_count || ratedMovies
  const avgRating = ratedMovies > 0 ? (movies.reduce((sum, m) => sum + m.rating, 0) / ratedMovies).toFixed(1) : 0

  // Filter movies for favorite picker
  const filteredMovies = movies
    .filter(m => m.title.toLowerCase().includes(favoriteSearch.toLowerCase()))
    .sort((a, b) => b.rating - a.rating)

  return (
    <div className="page profile-page">
      <div className="profile-header">
        <div className="profile-pic">
          <div className="pfp-placeholder">
            {getPfpUrl() ? (
              <img src={getPfpUrl()} alt="Profile" onError={(e) => {
                console.error('Failed to load PFP:', getPfpUrl())
                e.target.style.display = 'none'
              }} />
            ) : (
              <span>📷</span>
            )}
          </div>
          <label className="pfp-upload">
            Change
            <input type="file" accept="image/*" onChange={handlePfpUpload} hidden />
          </label>
        </div>

        <div className="profile-info">
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            <h1>{profile.display_name ? profile.display_name : `@${profile.username}`}</h1>
            <div style={{ color: 'var(--text-dim)', fontSize: 13 }}>{`@${profile.username}`}</div>
            <button className="toggle-btn" onClick={handleEditToggle}>Edit Profile</button>
            <button className="toggle-btn" onClick={handleSignOut}>Sign Out</button>
          </div>
          <p className="bio">{profile.bio || 'No bio yet'}</p>
          <div className="stats">
            <div className="stat">
              <span className="stat-value">{totalMovies}</span>
              <span className="stat-label">Movies</span>
            </div>
            <div className="stat">
              <span className="stat-value">{avgRating}</span>
              <span className="stat-label">Avg Rating</span>
            </div>
            <div className="stat">
              <span className="stat-value">{likedMovies.length}</span>
              <span className="stat-label">Hearted</span>
            </div>
          </div>
        </div>
      </div>

      {/* Favorite Movies */}
      <div className="section">
        <h2>Your 4 Favorites</h2>
        <div className="favorites-grid">
          {[0, 1, 2, 3].map((slot) => {
            const movieId = profile[`favorite_movie_${slot + 1}`]
            const movie = movies.find(m => m.tmdb_id === movieId)
            
            return (
              <div key={slot} className="favorite-slot">
                {movie ? (
                  <>
                    {movie.poster_path && (
                      <img src={movie.poster_path} alt={movie.title} />
                    )}
                    <div className="favorite-info">
                      <p>{movie.title}</p>
                      <button onClick={() => {
                        setShowFavoritePicker(slot)
                        setFavoriteSearch('')
                      }}>
                        Change
                      </button>
                    </div>
                  </>
                ) : (
                  <button
                    className="empty-favorite"
                    onClick={() => {
                      setShowFavoritePicker(slot)
                      setFavoriteSearch('')
                    }}
                  >
                    + Add
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {showFavoritePicker !== null && (
          <div className="modal-overlay" onClick={() => setShowFavoritePicker(null)}>
            <div className="modal favorites-picker" onClick={e => e.stopPropagation()}>
              <h3>Select Favorite #{showFavoritePicker + 1}</h3>
              <input
                type="text"
                className="favorites-search"
                placeholder="Search movies..."
                value={favoriteSearch}
                onChange={(e) => setFavoriteSearch(e.target.value)}
                autoFocus
              />
              <div className="favorites-list">
                {filteredMovies.length > 0 ? (
                  filteredMovies.map(movie => (
                    <div
                      key={movie.tmdb_id}
                      className="favorite-item"
                      onClick={() => handleSetFavorite(showFavoritePicker, movie.tmdb_id)}
                    >
                      {movie.poster_path && (
                        <img src={movie.poster_path} alt={movie.title} />
                      )}
                      <div>
                        <p>{movie.title}</p>
                        <p className="rating">⭐ {movie.rating}</p>
                      </div>
                    </div>
                  ))
                ) : (
                  <p style={{ textAlign: 'center', color: '#999' }}>No movies found</p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Rating Distribution */}
      <div className="section">
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <h2>Rating Distribution</h2>
          <button
            className="toggle-btn"
            onClick={() => setShowCounts(!showCounts)}
          >
            {showCounts ? 'Show %' : 'Show #'}
          </button>
        </div>

        <div className="rating-graph">
          {Object.entries(ratingDistribution).map(([rating, count]) => {
            const percentage = maxCount > 0 ? (count / maxCount) * 100 : 0
            const display = showCounts ? count : ((count / totalMovies) * 100).toFixed(0)
            const unit = showCounts ? '' : '%'
            
            return (
              <div key={rating} className="graph-bar">
                <div className="bar-value">{display}{unit}</div>
                <div className="bar-container" style={{ position: 'relative' }}>
                  <div
                    className="bar-fill"
                    style={{ height: `${percentage}%`, position: 'absolute', bottom: 0, width: '100%' }}
                  />
                </div>
                <div className="bar-label">{rating/2}⭐</div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Hearted Movies */}
      <div className="section">
        <div className="hearted-heading">
          <h2>❤️ Hearted Movies ({likedMovies.length})</h2>
          {likedMovies.length > 6 && (
            <button className="view-all-btn" onClick={() => setShowHeartedPage(true)}>
              →
            </button>
          )}
        </div>
        <div className="movie-list">
          {likedMovies.slice(0, 6).map(movie => (
            <div key={movie.tmdb_id} className="movie-item">
              {movie.poster_path && (
                <img src={movie.poster_path} alt={movie.title} />
              )}
              <div>
                <p>{movie.title}</p>
                <p className="rating">⭐ {movie.rating}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Import Letterboxd */}
      <div className="section">
        <h2>Import Letterboxd Data</h2>
        <label className="import-label">
          📦 Upload Letterboxd zip file
          <input
            type="file"
            accept=".zip"
            onChange={handleLetterboxdImport}
            disabled={importing}
            hidden
          />
        </label>
        {importing && <p>Importing...</p>}
      </div>

      {editing && (
        <div className="modal-overlay" onClick={() => setEditing(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Edit Profile</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <input value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} placeholder="Display name" />
              <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Username" />
              <textarea value={editBio} onChange={(e) => setEditBio(e.target.value)} placeholder="Short bio" />
              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
                <button onClick={handleSaveProfile} className="toggle-btn">Save</button>
                <button onClick={() => setEditing(false)} className="toggle-btn">Cancel</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// Hearted Movies Full Page Component
function HeartedMoviesPage({ onBack, movies }) {
  const [searchTerm, setSearchTerm] = useState('')
  
  const filteredMovies = movies.filter(m => 
    m.title.toLowerCase().includes(searchTerm.toLowerCase())
  )

  return (
    <div className="page hearted-page">
      <button className="back-btn" onClick={onBack}>← Back</button>
      
      <h1>❤️ Hearted Movies</h1>
      
      <input
        type="text"
        className="search-input"
        placeholder="Search hearted movies..."
        value={searchTerm}
        onChange={(e) => setSearchTerm(e.target.value)}
      />
      
      <div className="hearted-grid">
        {filteredMovies.map(movie => (
          <div key={movie.tmdb_id} className="hearted-item">
            {movie.poster_path && (
              <img src={movie.poster_path} alt={movie.title} />
            )}
            <div className="hearted-info">
              <p className="hearted-title">{movie.title}</p>
              <p className="hearted-year">({movie.release_year})</p>
              <p className="hearted-rating">⭐ {movie.rating}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
