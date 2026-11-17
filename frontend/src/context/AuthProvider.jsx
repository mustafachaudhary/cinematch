import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import { api } from '../hooks/api'

const AuthContext = createContext(null)

const normalize = (value) =>
  (value || '').trim().replace(/^@+/, '').replace(/\s+/g, '').toLowerCase()

function initialUser() {
  try {
    const stored = normalize(localStorage.getItem('cinematch_username'))
    // cinephile was removed; drop stale sessions instead of sending a 404 header.
    if (stored === 'cinephile') {
      localStorage.removeItem('cinematch_username')
      localStorage.removeItem('cinematch_display_name')
      return null
    }
    return stored || null
  } catch {
    return null
  }
}

function initialDisplayName() {
  try {
    return localStorage.getItem('cinematch_display_name') || ''
  } catch {
    return ''
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(initialUser)
  const [displayName, setDisplayName] = useState(initialDisplayName)

  const signIn = useCallback((profile) => {
    const username = normalize(profile?.username)
    if (!username) return
    const name = (profile?.display_name || '').trim()

    try {
      localStorage.setItem('cinematch_username', username)
      if (name) localStorage.setItem('cinematch_display_name', name)
      else localStorage.removeItem('cinematch_display_name')
    } catch {
      /* the in-memory session still works */
    }
    setUser(username)
    setDisplayName(name)
  }, [])

  const signOut = useCallback(async () => {
    try {
      await api.signout()
    } catch {
      /* ending the local session matters more than the server ack */
    }
    try {
      localStorage.removeItem('cinematch_username')
      localStorage.removeItem('cinematch_display_name')
    } catch {
      /* ignore */
    }
    setUser(null)
    setDisplayName('')
  }, [])

  const value = useMemo(
    () => ({ user, displayName, signIn, signOut }),
    [user, displayName, signIn, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
