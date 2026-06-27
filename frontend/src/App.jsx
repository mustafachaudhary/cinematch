import { useState } from 'react'
import HomeFeed from './pages/HomeFeed'
import SearchPage from './pages/SearchPage'
import LogsPage from './pages/LogsPage'
import ProfilePage from './pages/ProfilePage'
import './index.css'

const DEFAULT_USER = 'mustafa'

function normalizeUsername(value) {
  const trimmed = (value || '').trim()
  return trimmed.replace(/^@+/, '').replace(/\s+/g, '').toLowerCase()
}

export default function App() {
  const [savedUser] = useState(() => {
    const stored = localStorage.getItem('cinematch_username')
    const normalized = normalizeUsername(stored)
    if (normalized) {
      localStorage.setItem('cinematch_username', normalized)
      return normalized
    }
    localStorage.setItem('cinematch_username', DEFAULT_USER)
    return DEFAULT_USER
  })
  const [page, setPage] = useState('profile')
  const loggedInUser = savedUser

  return (
    <div className="app">
      <nav className="navbar">
        <div className="navbar-brand">
          <span style={{ marginRight: 8 }}>🎬</span>
          CineMatch
          {page === 'profile' && <span className="subdomain-label">profile.cinematch</span>}
          {page === 'search' && <span className="subdomain-label">search.cinematch</span>}
          {loggedInUser && (
            <span className="subdomain-label" style={{ marginLeft: 12 }}>
              @{loggedInUser}
            </span>
          )}
        </div>
        <div className="navbar-buttons">
          <button
            className={`nav-button ${page === 'home' ? 'active' : ''}`}
            onClick={() => setPage('home')}
          >
            Discover
          </button>
          <button
            className={`nav-button ${page === 'search' ? 'active' : ''}`}
            onClick={() => setPage('search')}
          >
            Search
          </button>
          <button
            className={`nav-button ${page === 'logs' ? 'active' : ''}`}
            onClick={() => setPage('logs')}
          >
            My Films
          </button>
          <button
            className={`nav-button ${page === 'profile' ? 'active' : ''}`}
            onClick={() => setPage('profile')}
          >
            Profile
          </button>
        </div>
      </nav>
      <main className="main-content">
        {page === 'home' && <HomeFeed />}
        {page === 'search' && <SearchPage />}
        {page === 'logs' && <LogsPage />}
        {page === 'profile' && <ProfilePage />}
      </main>
    </div>
  )
}
