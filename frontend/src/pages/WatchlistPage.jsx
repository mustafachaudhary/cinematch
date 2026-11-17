import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { formatCount, formatDate } from '../lib/format'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import PageHeader from '../components/PageHeader'
import Poster from '../components/Poster'
import Skeleton from '../components/Skeleton'
import { useToast } from '../components/Toast'
import styles from './WatchlistPage.module.css'

function WatchlistSkeleton() {
  return (
    <div className={styles.grid} aria-label="Loading watchlist">
      {Array.from({ length: 10 }, (_, index) => (
        <div key={index} className={styles.card}>
          <Skeleton ratio="2 / 3" />
          <Skeleton className={styles.skeletonTitle} />
          <Skeleton className={styles.skeletonLine} />
        </div>
      ))}
    </div>
  )
}

export default function WatchlistPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [films, setFilms] = useState(null)
  const [error, setError] = useState(null)
  useTitle('Watchlist')

  const load = async () => {
    try {
      setFilms(await api.getWatchlist())
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const remove = async (film) => {
    try {
      await api.removeFromWatchlist(film.tmdb_id)
      setFilms((list) => (list || []).filter((item) => item.tmdb_id !== film.tmdb_id))
      toast(`Removed “${film.title}” from the watchlist.`)
    } catch (err) {
      toast(err.message, 'error')
    }
  }

  const count = films?.length || 0
  const intro = films
    ? `${formatCount(count)} ${count === 1 ? 'film' : 'films'} set aside for later.`
    : 'Films set aside for later.'

  return (
    <div>
      <PageHeader
        title="The watchlist"
        intro={intro}
        action={
          <Button variant="secondary" onClick={() => navigate('/search')}>
            Find a film
          </Button>
        }
      />

      {error ? (
        <EmptyState
          title="The watchlist could not be read."
          action={
            <Button variant="secondary" onClick={load}>
              Try again
            </Button>
          }
        >
          {error}
        </EmptyState>
      ) : !films ? (
        <WatchlistSkeleton />
      ) : !count ? (
        <EmptyState
          title="Nothing waiting in the wings."
          action={<Button onClick={() => navigate('/')}>Browse Discover</Button>}
        >
          Open any film and choose “Add to watchlist” to keep it here for later.
        </EmptyState>
      ) : (
        <ul className={styles.grid}>
          {films.map((film) => (
            <li key={film.tmdb_id} className={styles.card}>
              <Link
                to={`/film/${film.tmdb_id}`}
                state={{ from: '/watchlist' }}
                className={styles.posterLink}
              >
                <Poster movie={film} ratio="poster" decorative />
              </Link>
              <div className={styles.copy}>
                <Link
                  to={`/film/${film.tmdb_id}`}
                  state={{ from: '/watchlist' }}
                  className={styles.title}
                >
                  {film.title}
                </Link>
                <p className={styles.meta}>
                  {[film.release_year, (film.genres || []).slice(0, 2).join(', ')]
                    .filter(Boolean)
                    .join(' \u00b7 ')}
                </p>
                <div className={styles.footer}>
                  <span className={styles.added}>
                    {film.added_at
                      ? `Added ${formatDate(film.added_at, { short: true })}`
                      : 'Saved for later'}
                  </span>
                  <button
                    type="button"
                    className={styles.remove}
                    onClick={() => remove(film)}
                    aria-label={`Remove ${film.title} from the watchlist`}
                  >
                    Remove
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
