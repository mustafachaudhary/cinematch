import { useState } from 'react'
import { api } from '../hooks/api'

export default function LoginPage({ onLogin }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)

  const normalize = (value) => {
    const trimmed = (value || '').trim()
    return trimmed.replace(/^@+/, '').replace(/\s+/g, '').toLowerCase()
  }
  const currentUser = localStorage.getItem('cinematch_username')

  const showMessage = (message, success = true) => {
    setStatus({ message, success })
  }

  const handleMode = (nextMode) => {
    setMode(nextMode)
    setStatus(null)
  }

  const handleCheck = async () => {
    const uname = normalize(username)
    if (!uname) {
      showMessage('Enter a username to check', false)
      return
    }

    setLoading(true)
    try {
      const result = await api.checkUser(uname)
      if (result.exists) {
        showMessage(`Username @${uname} exists. You can log in.`, true)
      } else {
        showMessage(`Username @${uname} is available for signup.`, true)
      }
    } catch (error) {
      showMessage('Unable to check username. Try again.', false)
    } finally {
      setLoading(false)
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    const uname = normalize(username)
    if (!uname) {
      showMessage('Username is required', false)
      return
    }
    if (!password) {
      showMessage('Password is required', false)
      return
    }

    setLoading(true)
    setStatus(null)
    try {
      if (mode === 'signup') {
        await api.signup({ username: uname, password })
        localStorage.setItem('cinematch_username', uname)
        localStorage.removeItem('cinematch_display_name')
        setDisplayName('')
        showMessage(`Signed up as @${uname}. Set your display name in profile settings.`, true)
        if (onLogin) onLogin(uname)
      } else {
        const profile = await api.login({ username: uname, password })
        localStorage.setItem('cinematch_username', profile.username)
        if (profile.display_name) {
          localStorage.setItem('cinematch_display_name', profile.display_name)
          setDisplayName(profile.display_name)
        } else {
          localStorage.removeItem('cinematch_display_name')
          setDisplayName('')
        }
        showMessage(`Welcome back, @${profile.username}`, true)
        if (onLogin) onLogin(profile.username)
      }
    } catch (error) {
      showMessage(error.message || 'Account action failed', false)
    } finally {
      setLoading(false)
    }
  }

  const logout = () => {
    localStorage.removeItem('cinematch_username')
    localStorage.removeItem('cinematch_display_name')
    setUsername('')
    setPassword('')
    setDisplayName('')
    setStatus({ message: 'Signed out locally.', success: true })
    if (onLogin) onLogin(null)
  }

  return (
    <div className="page" style={{ maxWidth: 520 }}>
      <h1>Account</h1>
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <button
          className={mode === 'login' ? 'btn-primary' : 'btn-secondary'}
          type="button"
          onClick={() => handleMode('login')}
        >
          Login
        </button>
        <button
          className={mode === 'signup' ? 'btn-primary' : 'btn-secondary'}
          type="button"
          onClick={() => handleMode('signup')}
        >
          Sign Up
        </button>
      </div>

      <form onSubmit={submit} style={{ display: 'grid', gap: 14 }}>
        <label>Username</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="e.g. mustafa"
          autoComplete="username"
        />

        <label>Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
          autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
        />

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn-primary" type="submit" disabled={loading}>
            {mode === 'signup' ? 'Create Account' : 'Log In'}
          </button>
          <button type="button" className="btn-secondary" onClick={handleCheck} disabled={loading}>
            Check Availability
          </button>
          <button type="button" className="btn-tertiary" onClick={logout}>
            Sign Out
          </button>
        </div>
      </form>

      {status && (
        <div style={{
          marginTop: 18,
          padding: 14,
          borderRadius: 12,
          background: status.success ? 'rgba(56, 142, 60, 0.12)' : 'rgba(183, 28, 28, 0.12)',
          color: status.success ? '#1b5e20' : '#b71c1c',
        }}>
          {status.message}
        </div>
      )}

      {currentUser && (
        <div style={{ marginTop: 24, color: 'var(--text-dim)' }}>
          Currently saved user: <strong>@{currentUser}</strong>
        </div>
      )}
    </div>
  )
}
