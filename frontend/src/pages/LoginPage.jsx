import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { SHOWCASE } from '../data/showcase'
import Brand from '../components/Brand'
import Button from '../components/Button'
import ThemeToggle from '../components/ThemeToggle'
import { useAuth } from '../context/AuthProvider'
import styles from './LoginPage.module.css'

const normalizeUsername = (value) =>
  (value || '').trim().replace(/^@+/, '').replace(/\s+/g, '').toLowerCase()

const MIN_PASSWORD = 8

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { signIn } = useAuth()
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [availability, setAvailability] = useState(null)
  const checkTimer = useRef(null)
  const isSignup = mode === 'signup'
  const uname = normalizeUsername(username)
  useTitle(isSignup ? 'Create account' : 'Sign in')

  useEffect(() => () => clearTimeout(checkTimer.current), [])

  const switchMode = (next) => {
    clearTimeout(checkTimer.current)
    setMode(next)
    setError('')
    setAvailability(null)
  }

  const onUsernameChange = (event) => {
    const raw = event.target.value
    setUsername(raw)
    setError('')
    if (!isSignup) return
    clearTimeout(checkTimer.current)
    const candidate = normalizeUsername(raw)
    if (!candidate) {
      setAvailability(null)
      return
    }
    setAvailability('checking')
    checkTimer.current = setTimeout(async () => {
      try {
        const res = await api.checkUser(candidate)
        setAvailability(res.exists ? 'taken' : 'free')
      } catch {
        setAvailability(null)
      }
    }, 350)
  }

  const onPasswordChange = (event) => {
    setPassword(event.target.value)
    setError('')
  }

  const submit = async (event) => {
    event.preventDefault()
    if (busy) return
    if (!uname || !password) return
    if (isSignup && password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`)
      return
    }
    setBusy(true)
    setError('')
    try {
      const profile = isSignup
        ? await api.signup({ username: uname, password })
        : await api.login({ username: uname, password })
      signIn(profile)
      const from = location.state?.from
      const destination = from && !from.startsWith('/login') ? from : '/'
      navigate(destination, { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const posters = SHOWCASE.filter((film) => film.poster_path)
  const wides = SHOWCASE.filter((film) => film.backdrop_path)

  return (
    <main className={styles.screen}>
      <section className={styles.gallery}>
        <header className={styles.galleryTop}>
          <Brand to="/login" tagline="Private film archive" />
          <ThemeToggle />
        </header>

        <div className={styles.wall} aria-hidden="true">
          <div className={styles.wallCol}>
            {posters[0] ? (
              <img src={posters[0].poster_path} alt="" loading="eager" />
            ) : null}
            {(wides[3] || wides[0]) ? (
              <img
                className={styles.wallWide}
                src={(wides[3] || wides[0]).backdrop_path}
                alt=""
                loading="eager"
              />
            ) : null}
          </div>
          <div className={styles.wallCol}>
            {wides[0] ? (
              <img
                className={styles.wallWide}
                src={wides[0].backdrop_path}
                alt=""
                loading="eager"
              />
            ) : null}
            {posters[1] ? (
              <img src={posters[1].poster_path} alt="" loading="eager" />
            ) : null}
          </div>
          <div className={styles.wallCol}>
            {posters[2] ? (
              <img src={posters[2].poster_path} alt="" loading="eager" />
            ) : null}
            {wides[1] ? (
              <img
                className={styles.wallWide}
                src={wides[1].backdrop_path}
                alt=""
                loading="eager"
              />
            ) : null}
          </div>
        </div>

        <div className={styles.mobileStrip} aria-hidden="true">
          {posters.slice(0, 6).map((film) => (
            <img key={film.tmdb_id} src={film.poster_path} alt="" loading="eager" />
          ))}
        </div>

        <footer className={styles.wallFoot}>
          <p className={styles.wallCaption}>Every print is a film in the collection.</p>
          <p className={styles.wallCredit}>Film data &amp; artwork from TMDB.</p>
        </footer>
      </section>

      <section className={styles.desk}>
        <div className={styles.deskInner}>
          <h1 className={styles.title}>
            {isSignup ? 'Create your account.' : 'Welcome back.'}
          </h1>
          <p className={styles.intro}>
            {isSignup
              ? 'Rate films, build a taste profile, and get recommendations that explain themselves.'
              : 'Sign in to pick up your taste profile where you left it.'}
          </p>

          <form className={styles.form} onSubmit={submit}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor={`${mode}-username`}>
                Username
              </label>
              <input
                id={`${mode}-username`}
                name={`${mode}-username`}
                type="text"
                className={styles.input}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="e.g. @mustafa"
                required
                value={username}
                onChange={onUsernameChange}
              />
              {isSignup && uname ? (
                <p
                  className={styles.availability}
                  data-state={availability}
                  aria-live="polite"
                >
                  {availability === 'checking'
                    ? `Checking @${uname}…`
                    : availability === 'free'
                      ? `@${uname} is available.`
                      : availability === 'taken'
                        ? `@${uname} is already taken.`
                        : ''}
                </p>
              ) : null}
            </div>

            <div className={styles.field}>
              <label className={styles.label} htmlFor={`${mode}-password`}>
                Password
              </label>
              <div className={styles.passwordWrap}>
                <input
                  id={`${mode}-password`}
                  name={`${mode}-password`}
                  type={showPw ? 'text' : 'password'}
                  className={`${styles.input} ${styles.passwordInput}`}
                  autoComplete={isSignup ? 'new-password' : 'current-password'}
                  placeholder={isSignup ? `${MIN_PASSWORD}+ characters` : 'Your password'}
                  required
                  value={password}
                  onChange={onPasswordChange}
                  aria-describedby={isSignup ? 'pw-rules' : undefined}
                />
                <button
                  type="button"
                  className={styles.showPassword}
                  onClick={() => setShowPw((value) => !value)}
                  aria-label={
                    showPw
                      ? 'Hide password'
                      : 'Show password as plain text. Warning: your password will be visible on screen.'
                  }
                  aria-pressed={showPw}
                >
                  {showPw ? 'Hide' : 'Show'}
                </button>
              </div>
              {isSignup && password.length > 0 && password.length < MIN_PASSWORD ? (
                <p className={styles.hint} id="pw-rules">
                  {MIN_PASSWORD - password.length} more character
                  {MIN_PASSWORD - password.length === 1 ? '' : 's'} to go. Length beats
                  complexity.
                </p>
              ) : null}
            </div>

            {error ? (
              <p className={styles.error} role="alert">
                {error}
              </p>
            ) : null}

            <Button type="submit" full className={styles.submit} disabled={busy}>
              {busy
                ? isSignup
                  ? 'Creating…'
                  : 'Signing in…'
                : isSignup
                  ? 'Create account'
                  : 'Sign in'}
            </Button>
          </form>

          <div className={styles.switch}>
            {isSignup ? (
              <>
                <span>Already have an account?</span>
                <button
                  type="button"
                  className={styles.switchButton}
                  onClick={() => switchMode('login')}
                >
                  Sign in
                </button>
              </>
            ) : (
              <>
                <span>First time here?</span>
                <button
                  type="button"
                  className={styles.switchButton}
                  onClick={() => switchMode('signup')}
                >
                  Create an account
                </button>
              </>
            )}
          </div>

          <p className={styles.privacy}>
            Your ratings train your own recommendations. Nothing leaves this machine.
          </p>
        </div>
      </section>
    </main>
  )
}
