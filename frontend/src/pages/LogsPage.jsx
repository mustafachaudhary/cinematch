import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { formatCount, formatDate } from '../lib/format'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import LogFilmDialog from '../components/LogFilmDialog'
import PageHeader from '../components/PageHeader'
import SectionHeader from '../components/SectionHeader'
import Skeleton from '../components/Skeleton'
import { StaticStars } from '../components/Stars'
import { useToast } from '../components/Toast'
import styles from './LogsPage.module.css'

function Thumb({ log }) {
  return (
    <span className={styles.thumb}>
      {log.poster_path ? (
        <img src={log.poster_path} alt="" loading="lazy" decoding="async" />
      ) : (
        <span className={styles.thumbFallback} aria-hidden="true">
          {log.title.slice(0, 1)}
        </span>
      )}
    </span>
  )
}

export default function LogsPage() {
  const toast = useToast()
  const [logs, setLogs] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')
  const [visible, setVisible] = useState(24)
  const [logOpen, setLogOpen] = useState(false)
  useTitle('The archive')

  const load = async () => {
    try {
      setLogs(await api.getLogs())
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const count = logs?.length || 0
  const average = count
    ? logs.reduce((sum, log) => sum + Number(log.rating || 0), 0) / count
    : 0
  const hearted = logs?.filter((log) => log.rating >= 4.5).length || 0
  const fiveStar = logs?.filter((log) => log.rating === 5).length || 0

  const needle = filter.trim().toLowerCase()
  const filtered = (logs || []).filter((log) => {
    if (!needle) return true
    return [log.title, log.release_year, ...(log.genres || [])]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(needle)
  })
  const shown = filtered.slice(0, visible)
  const remaining = Math.max(0, filtered.length - shown.length)

  const remove = async (log) => {
    try {
      await api.removeLog(log.tmdb_id)
      setLogs((list) => (list || []).filter((item) => item.tmdb_id !== log.tmdb_id))
      toast(`Removed “${log.title}” from the archive.`)
    } catch (err) {
      toast(err.message, 'error')
    }
  }

  const intro = logs
    ? `${formatCount(count)} films, kept and rated.`
    : 'Your ratings, in one ledger.'

  return (
    <div>
      <PageHeader
        title="The archive"
        intro={intro}
        action={<Button onClick={() => setLogOpen(true)}>Log a film</Button>}
      />

      {error ? (
        <EmptyState
          title="The archive could not be read."
          action={
            <Button variant="secondary" onClick={load}>
              Try again
            </Button>
          }
        >
          {error}
        </EmptyState>
      ) : (
        <div className={styles.ledger}>
          <aside className={styles.aside}>
            <h2 className={styles.asideTitle}>In numbers</h2>
            <dl className={styles.stats}>
              <div className={styles.stat}>
                <dt>films in the archive</dt>
                <dd>{logs ? formatCount(count) : <Skeleton className={styles.statSkeleton} />}</dd>
              </div>
              <div className={styles.stat}>
                <dt>average rating, out of five</dt>
                <dd>{logs ? average.toFixed(2) : <Skeleton className={styles.statSkeleton} />}</dd>
              </div>
              <div className={styles.stat}>
                <dt>hearted at 4.5 or more</dt>
                <dd>{logs ? formatCount(hearted) : <Skeleton className={styles.statSkeleton} />}</dd>
              </div>
              <div className={styles.stat}>
                <dt>five-star ratings</dt>
                <dd>{logs ? formatCount(fiveStar) : <Skeleton className={styles.statSkeleton} />}</dd>
              </div>
            </dl>
          </aside>

          <section className={styles.tableSection} aria-labelledby="logbook-title">
            <SectionHeader
              id="logbook-title"
              title="The logbook"
              action={logs ? `${formatCount(filtered.length)} films` : undefined}
            />

            <div className={styles.toolbar}>
              <label className={styles.srOnly} htmlFor="archive-filter">
                Filter the archive
              </label>
              <input
                id="archive-filter"
                className={styles.filter}
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value)
                  setVisible(24)
                }}
                placeholder="Filter by title, genre, or year"
                autoComplete="off"
              />
            </div>

            {!logs ? (
              <div className={styles.loading} aria-label="Loading archive">
                {Array.from({ length: 9 }, (_, index) => (
                  <div key={index} className={styles.loadingRow}>
                    <Skeleton className={styles.loadingThumb} ratio="2 / 3" />
                    <Skeleton className={styles.loadingCopy} />
                    <Skeleton className={styles.loadingMeta} />
                  </div>
                ))}
              </div>
            ) : !count ? (
              <EmptyState
                title="An empty archive."
                action={<Button onClick={() => setLogOpen(true)}>Log your first film</Button>}
              >
                Rate a film and it will take its place here. The archive is the evidence
                the recommendation engine reads.
              </EmptyState>
            ) : !filtered.length ? (
              <EmptyState
                title={`Nothing matches “${filter.trim()}”.`}
                action={
                  <Button variant="secondary" onClick={() => setFilter('')}>
                    Clear the filter
                  </Button>
                }
              >
                Try a shorter title, a genre, or a release year.
              </EmptyState>
            ) : (
              <>
                <div className={styles.tableWrap}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th scope="col">Film</th>
                        <th scope="col" className={styles.genreCell}>
                          Genre
                        </th>
                        <th scope="col">Rating</th>
                        <th scope="col" className={styles.loggedCell}>
                          Logged
                        </th>
                        <th scope="col" className={styles.actionCell}>
                          <span className={styles.srOnly}>Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((log) => (
                        <tr key={log.tmdb_id}>
                          <td>
                            <Link
                              to={`/film/${log.tmdb_id}`}
                              state={{ from: '/films' }}
                              className={styles.film}
                            >
                              <Thumb log={log} />
                              <span className={styles.filmCopy}>
                                <span className={styles.filmTitle}>{log.title}</span>
                                <span className={styles.filmMeta}>
                                  {log.release_year || 'Year unknown'}
                                </span>
                                <span className={styles.mobileMeta}>
                                  {[
                                    log.release_year,
                                    (log.genres || []).slice(0, 2).join(', '),
                                  ]
                                    .filter(Boolean)
                                    .join(' \u00b7 ')}
                                </span>
                              </span>
                            </Link>
                          </td>
                          <td className={`${styles.mutedCell} ${styles.genreCell}`}>
                            {(log.genres || []).slice(0, 2).join(', ') || '—'}
                          </td>
                          <td>
                            <span className={styles.rating}>
                              <StaticStars value={log.rating} size={12} />
                              <span className={styles.ratingValue}>
                                {Number(log.rating).toFixed(1)}
                              </span>
                            </span>
                          </td>
                          <td className={`${styles.mutedCell} ${styles.loggedCell}`}>
                            {formatDate(log.logged_at, { short: true }) || '—'}
                          </td>
                          <td className={styles.actionCell}>
                            <button
                              type="button"
                              className={styles.remove}
                              onClick={() => remove(log)}
                              aria-label={`Remove ${log.title} from the archive`}
                              title="Remove from archive"
                            >
                              <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                                <path
                                  d="M3 3l10 10M13 3L3 13"
                                  stroke="currentColor"
                                  strokeWidth="1.3"
                                />
                              </svg>
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className={styles.more}>
                  <p className={styles.moreText}>
                    Showing {shown.length} of {formatCount(filtered.length)}
                  </p>
                  {remaining ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setVisible((value) => value + 24)}
                    >
                      Show 24 more
                    </Button>
                  ) : null}
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {logOpen ? (
        <LogFilmDialog onClose={() => setLogOpen(false)} onLogged={load} />
      ) : null}
    </div>
  )
}
