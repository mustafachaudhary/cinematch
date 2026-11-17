import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import {
  formatCount,
  formatDate,
  formatMoney,
  formatRuntime,
  initials,
  plainReason,
} from '../lib/format'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import LogModal from '../components/LogModal'
import PageHeader from '../components/PageHeader'
import Poster from '../components/Poster'
import PosterRun from '../components/PosterRun'
import SectionHeader from '../components/SectionHeader'
import Skeleton from '../components/Skeleton'
import { StaticStars } from '../components/Stars'
import { useToast } from '../components/Toast'
import styles from './MoviePage.module.css'

const names = (people = [], limit = 3) => {
  const seen = new Set()
  const list = []
  for (const person of people) {
    const name = person?.name
    if (!name || seen.has(name)) continue
    seen.add(name)
    list.push(name)
    if (list.length === limit) break
  }
  return list.join(', ')
}

function CastPortrait({ person }) {
  if (person.profile_path) {
    return (
      <span className={styles.castPortrait}>
        <img src={person.profile_path} alt="" loading="lazy" decoding="async" />
      </span>
    )
  }
  return (
    <span className={`${styles.castPortrait} ${styles.castFallback}`} aria-hidden="true">
      {initials(person.name)}
    </span>
  )
}

function MovieSkeleton() {
  return (
    <div>
      <h1 className={styles.srOnly}>Film</h1>
      <Skeleton className={styles.skeletonBack} />
      <div className={styles.skeletonHero}>
        <Skeleton className={styles.skeletonPoster} ratio="2 / 3" />
        <div className={styles.skeletonCopy}>
          <Skeleton className={styles.skeletonTitle} />
          <Skeleton className={styles.skeletonLine} />
          <Skeleton className={styles.skeletonLineShort} />
          <Skeleton className={styles.skeletonMatch} />
        </div>
        <Skeleton className={styles.skeletonBackdrop} ratio="16 / 10" />
      </div>
    </div>
  )
}

export default function MoviePage() {
  const { tmdbId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const toast = useToast()
  const [movie, setMovie] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [logOpen, setLogOpen] = useState(false)
  const [feedbackBusy, setFeedbackBusy] = useState(false)
  const [watchlistBusy, setWatchlistBusy] = useState(false)
  const [inWatchlist, setInWatchlist] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  useTitle(movie?.title || 'Film')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    setInWatchlist(false)
    api
      .getMovie(tmdbId)
      .then((data) => {
        if (active) {
          setMovie(data)
          setInWatchlist(Boolean(data.in_watchlist))
        }
      })
      .catch((err) => {
        if (active) setError(err.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [tmdbId, reloadKey])

  const from = location.state?.from
  const backLabel = from?.startsWith('/search')
    ? 'Back to results'
    : from?.startsWith('/films')
      ? 'Back to the archive'
      : from?.startsWith('/watchlist')
        ? 'Back to watchlist'
        : from?.startsWith('/profile')
          ? 'Back to profile'
          : from?.startsWith('/actor/')
            ? 'Back to filmography'
            : from?.startsWith('/film/')
              ? 'Back'
              : 'Back to Discover'

  const goBack = () => {
    // Preserve the previous page's own state so film -> actor -> film keeps
    // the original search/feed origin instead of turning into a loop.
    if (from) navigate(from, { state: location.state?.previous })
    else navigate('/')
  }

  const hideFromFeed = async () => {
    setFeedbackBusy(true)
    try {
      await api.setFeedback(movie.tmdb_id, 'not_interested')
      toast('Noted — we’ll keep this one out of your feed.')
      navigate('/')
    } catch (err) {
      toast(err.message, 'error')
    } finally {
      setFeedbackBusy(false)
    }
  }

  const toggleWatchlist = async () => {
    setWatchlistBusy(true)
    try {
      if (inWatchlist) {
        await api.removeFromWatchlist(movie.tmdb_id)
        setInWatchlist(false)
        toast(`Removed “${movie.title}” from the watchlist.`)
      } else {
        await api.addToWatchlist(movie.tmdb_id)
        setInWatchlist(true)
        toast(`Added “${movie.title}” to the watchlist.`)
      }
    } catch (err) {
      toast(err.message, 'error')
    } finally {
      setWatchlistBusy(false)
    }
  }

  if (loading && !movie) return <MovieSkeleton />

  if (error) {
    return (
      <div>
        <button type="button" className={styles.back} onClick={goBack}>
          <ArrowLeft />
          {backLabel}
        </button>
        <PageHeader title="Film not found" />
        <EmptyState
          title="This print is missing."
          action={
            <Button variant="secondary" onClick={goBack}>
              {backLabel}
            </Button>
          }
        >
          {error}
        </EmptyState>
      </div>
    )
  }

  if (!movie) return null

  const match = movie.match
  const trailer =
    movie.videos?.find((video) => video.type === 'Trailer' && video.official) ||
    movie.videos?.find((video) => video.type === 'Trailer') ||
    movie.videos?.[0]
  const writers = names(movie.writers, 3)
  const directors = names(movie.directors, 3)
  const cast = movie.cast || []
  const stills = (movie.images?.backdrops || []).slice(0, 3)
  const similar = (movie.similar || []).slice(0, 8)
  const themes = Array.isArray(movie.themes) ? movie.themes : []
  const rawThemes = themes.length ? themes : (movie.keywords || [])
  const keywords = Array.from(
    new Set(
      rawThemes
        .map((theme) => String(theme || '').trim())
        .filter(Boolean),
    ),
  ).slice(0, 8)
  const subniches = Array.from(
    new Set(
      (Array.isArray(movie.subniches) ? movie.subniches : [])
        .map((cluster) => String(cluster || '').split(' \u00b7 ')[0].trim())
        .filter(Boolean),
    ),
  )

  const facts = [
    ['Director', directors || null],
    ['Writers', writers || null],
    ['Starring', names(cast, 3) || null],
    ['Runtime', formatRuntime(movie.runtime)],
    ['Release', formatDate(movie.release_date)],
    ['Country', (movie.production_countries || []).slice(0, 2).join(', ') || null],
    ['Language', (movie.spoken_languages || []).slice(0, 2).join(', ') || null],
    ['Certification', movie.certification],
  ].filter(([, value]) => Boolean(value))

  const details = [
    ['Director', directors || null],
    ['Writers', writers || null],
    ['Runtime', formatRuntime(movie.runtime)],
    ['Release date', formatDate(movie.release_date)],
    ['Certification', movie.certification],
    [
      'Spoken languages',
      (movie.spoken_languages || []).slice(0, 3).join(', ') || null,
    ],
    [
      'Production countries',
      (movie.production_countries || []).slice(0, 3).join(', ') || null,
    ],
    [
      'Production',
      (movie.production_companies || [])
        .slice(0, 3)
        .map((company) => company.name)
        .join(', ') || null,
    ],
    ['Budget', formatMoney(movie.budget)],
    ['Box office', formatMoney(movie.revenue)],
    ['Status', movie.status],
    [
      'TMDB rating',
      movie.vote_average
        ? `${Number(movie.vote_average).toFixed(1)} / 10 from ${formatCount(movie.vote_count)} ratings`
        : null,
    ],
  ].filter(([, value]) => Boolean(value))

  return (
    <div>
      <button type="button" className={styles.back} onClick={goBack}>
        <ArrowLeft />
        {backLabel}
      </button>

      <article>
        <div className={styles.hero}>
          <div className={styles.posterCol}>
            <Poster movie={movie} className={styles.poster} loading="eager" />
            {movie.user_rating ? (
              <div className={styles.yourRating}>
                <span className={styles.dataLabel}>Your rating</span>
                <StaticStars value={movie.user_rating} size={15} showValue />
                <span className={styles.loggedDate}>
                  Logged {formatDate(movie.logged_at) || 'earlier'}
                </span>
              </div>
            ) : null}
            <Button full onClick={() => setLogOpen(true)}>
              {movie.user_rating ? 'Update rating' : 'Log film'}
            </Button>
            <Button
              full
              variant="secondary"
              className={inWatchlist ? styles.watchlistSaved : ''}
              onClick={toggleWatchlist}
              disabled={watchlistBusy}
              aria-pressed={inWatchlist}
            >
              <Bookmark filled={inWatchlist} />
              {watchlistBusy
                ? 'Saving…'
                : inWatchlist
                  ? 'In watchlist'
                  : 'Add to watchlist'}
            </Button>
            <Button
              full
              variant="text"
              onClick={hideFromFeed}
              disabled={feedbackBusy}
            >
              {feedbackBusy ? 'Saving…' : 'Not interested'}
            </Button>
            {trailer ? (
              <a
                className={styles.trailer}
                href={`https://www.youtube.com/watch?v=${trailer.key}`}
                target="_blank"
                rel="noreferrer"
              >
                Watch the trailer
              </a>
            ) : null}
          </div>

          <div className={styles.titleCol}>
            <h1 className={styles.title}>{movie.title}</h1>
            {movie.original_title && movie.original_title !== movie.title ? (
              <p className={styles.original}>{movie.original_title}</p>
            ) : null}
            {movie.tagline ? <p className={styles.tagline}>“{movie.tagline}”</p> : null}

            <p className={styles.meta}>
              {[
                movie.release_year,
                formatRuntime(movie.runtime),
                movie.certification,
              ]
                .filter(Boolean)
                .join(' \u00b7 ')}
            </p>
            <p className={styles.genres}>{(movie.genres || []).join(', ')}</p>

            {movie.overview ? (
              <p className={styles.synopsisLead}>{movie.overview}</p>
            ) : null}

            {match ? (
              <div className={styles.match}>
                <span className={styles.matchScore}>
                  {Math.round(match.score)}
                  <span className={styles.matchMark}>%</span>
                </span>
                <div className={styles.matchCopy}>
                  <span className={styles.matchLabel}>match for your taste</span>
                  <p className={styles.matchReason}>{plainReason(match.reason)}</p>
                </div>
              </div>
            ) : movie.vote_average ? (
              <div className={styles.tmdbMatch}>
                <span className={styles.tmdbScore}>
                  {Number(movie.vote_average).toFixed(1)}
                </span>
                <span className={styles.matchLabel}>
                  TMDB average from {formatCount(movie.vote_count)} ratings
                </span>
              </div>
            ) : null}
          </div>

          <div className={styles.asideCol}>
            {movie.backdrop_path ? (
              <figure>
                <Poster movie={movie} src={movie.backdrop_path} ratio="wide" decorative />
                <figcaption className={styles.caption}>
                  A still from {movie.title}.
                </figcaption>
              </figure>
            ) : null}

            {facts.length ? (
              <dl className={styles.facts}>
                {facts.map(([label, value]) => (
                  <div key={label} className={styles.fact}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </div>
        </div>

        {movie.user_rating ? (
          <section className={styles.section} aria-labelledby="history-title">
            <SectionHeader
              id="history-title"
              title="Your history"
              action={
                <Button variant="text" onClick={() => setLogOpen(true)}>
                  Edit log
                </Button>
              }
            />
            <div className={styles.history}>
              <div className={styles.historyRating}>
                <span className={styles.dataLabel}>You rated it</span>
                <StaticStars value={movie.user_rating} size={19} showValue />
                <span className={styles.loggedDate}>
                  {formatDate(movie.logged_at) || 'Logged earlier'}
                </span>
              </div>
              <div className={styles.historyCopy}>
                {movie.user_review ? (
                  <>
                    <blockquote className={styles.quote}>
                      “{movie.user_review.content}”
                    </blockquote>
                    <p className={styles.reviewMeta}>
                      {movie.user_review.label} note
                      {movie.user_review.created_at
                        ? ` \u00b7 ${formatDate(movie.user_review.created_at)}`
                        : ''}
                    </p>
                  </>
                ) : (
                  <p className={styles.noReview}>
                    You didn’t leave a note. An edit is one click away.
                  </p>
                )}
              </div>
            </div>
          </section>
        ) : null}

        {keywords.length ? (
          <section className={styles.section} aria-labelledby="themes-title">
            <SectionHeader
              id="themes-title"
              title="Themes"
              description="The currents the archive files this print under."
              action={
                <span className={styles.themeCount}>
                  {String(keywords.length).padStart(2, '0')}
                </span>
              }
            />
            <ol className={styles.themeList}>
              {keywords.map((theme, index) => (
                <li
                  key={theme}
                  className={styles.themeItem}
                  data-lead={index === 0 ? 'true' : undefined}
                >
                  <span className={styles.themeIndex} aria-hidden="true">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className={styles.themeLabel}>{theme}</span>
                </li>
              ))}
            </ol>
            {subniches.length ? (
              <div className={styles.themeClusters}>
                <span className={styles.clusterLabel}>Filed under</span>
                <span className={styles.clusterList}>
                  {subniches.map((cluster) => (
                    <span key={cluster} className={styles.cluster}>
                      {cluster}
                    </span>
                  ))}
                </span>
              </div>
            ) : null}
          </section>
        ) : null}

        {cast.length ? (
          <section className={styles.section} aria-labelledby="cast-title">
            <SectionHeader
              id="cast-title"
              title="Cast"
              description="Top-billed players, in order."
            />
            <div className={styles.castRun}>
              {cast.slice(0, 8).map((person, index) => {
                const content = (
                  <>
                    <CastPortrait person={person} />
                    <p className={styles.castName}>{person.name}</p>
                    {person.character ? (
                      <p className={styles.character}>{person.character}</p>
                    ) : null}
                  </>
                )
                const key = `${person.id || person.name}-${index}`
                return person.id ? (
                  <Link
                    key={key}
                    to={`/actor/${person.id}`}
                    state={{
                      from: `${location.pathname}${location.search}`,
                      previous: location.state,
                    }}
                    className={styles.person}
                    data-lead={index === 0 ? 'true' : undefined}
                  >
                    {content}
                  </Link>
                ) : (
                  <div
                    key={key}
                    className={styles.person}
                    data-lead={index === 0 ? 'true' : undefined}
                  >
                    {content}
                  </div>
                )
              })}
            </div>
          </section>
        ) : null}

        {stills.length ? (
          <section className={styles.section} aria-labelledby="stills-title">
            <SectionHeader
              id="stills-title"
              title="Stills"
              description="Frames from the film, via TMDB."
            />
            <div className={styles.stills} data-count={stills.length}>
              {stills.map((still, index) => (
                <figure key={still} className={styles.still}>
                  <img
                    src={still}
                    alt=""
                    loading={index === 0 ? 'eager' : 'lazy'}
                    decoding="async"
                  />
                </figure>
              ))}
            </div>
          </section>
        ) : null}

        {details.length ? (
          <section className={styles.section} aria-labelledby="details-title">
            <SectionHeader id="details-title" title="Details" />
            <dl className={styles.detailsGrid}>
              {details.map(([label, value]) => (
                <div key={label} className={styles.detail}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <div className={styles.externalLinks}>
              <a
                href={`https://www.themoviedb.org/movie/${movie.tmdb_id}`}
                target="_blank"
                rel="noreferrer"
              >
                TMDB record
              </a>
              {movie.imdb_id ? (
                <a
                  href={`https://www.imdb.com/title/${movie.imdb_id}/`}
                  target="_blank"
                  rel="noreferrer"
                >
                  IMDb record
                </a>
              ) : null}
              {movie.homepage ? (
                <a href={movie.homepage} target="_blank" rel="noreferrer">
                  Official site
                </a>
              ) : null}
            </div>
          </section>
        ) : null}

        {similar.length ? (
          <section className={styles.section} aria-labelledby="similar-title">
            <SectionHeader
              id="similar-title"
              title="More like this"
              description="What sits alongside it on the shelf."
            />
            <PosterRun
              films={similar}
              liftedIndex={1}
              ariaLabel="Similar films"
              fact={(film) => film.release_year || ''}
            />
          </section>
        ) : null}
      </article>

      {logOpen ? (
        <LogModal
          movie={movie}
          onClose={() => setLogOpen(false)}
          onSaved={() => {
            setLogOpen(false)
            setReloadKey((key) => key + 1)
          }}
        />
      ) : null}
    </div>
  )
}

function ArrowLeft() {
  return (
    <svg viewBox="0 0 18 12" width="16" height="11" fill="none" aria-hidden="true">
      <path d="M17 6H1M6 1L1 6l5 5" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}

function Bookmark({ filled = false }) {
  return (
    <svg viewBox="0 0 12 15" width="11" height="14" aria-hidden="true">
      <path
        d="M1.5 1.5h9v12L6 10.5l-4.5 3v-12Z"
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
    </svg>
  )
}
