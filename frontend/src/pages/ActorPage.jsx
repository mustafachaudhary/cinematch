import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { initials } from '../lib/format'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import Poster from '../components/Poster'
import SectionHeader from '../components/SectionHeader'
import Skeleton from '../components/Skeleton'
import { StaticStars } from '../components/Stars'
import styles from './ActorPage.module.css'

const titleCollator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: 'base',
})

const asNumber = (value) => {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const asTimestamp = (value) => {
  if (!value) return null
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : null
}

function compareNumbers(a, b, direction = 'desc') {
  const left = asNumber(a)
  const right = asNumber(b)
  if (left === null && right === null) return 0
  if (left === null) return 1
  if (right === null) return -1
  return direction === 'desc' ? right - left : left - right
}

function tieBreak(a, b) {
  return (
    compareNumbers(a.release_year, b.release_year, 'desc') ||
    titleCollator.compare(a.title || '', b.title || '')
  )
}

const withTieBreak = (compare) => (a, b) => compare(a, b) || tieBreak(a, b)

const SORT_OPTIONS = [
  {
    value: 'newest',
    label: 'Newest release',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.release_year, b.release_year, 'desc'),
    ),
  },
  {
    value: 'oldest',
    label: 'Oldest release',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.release_year, b.release_year, 'asc'),
    ),
  },
  {
    value: 'rating',
    label: 'Highest TMDB rating',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.vote_average, b.vote_average, 'desc'),
    ),
  },
  {
    value: 'rating-low',
    label: 'Lowest TMDB rating',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.vote_average, b.vote_average, 'asc'),
    ),
  },
  {
    value: 'user-rating',
    label: 'Your highest rating',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.user_rating, b.user_rating, 'desc'),
    ),
  },
  {
    value: 'user-rating-low',
    label: 'Your lowest rating',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.user_rating, b.user_rating, 'asc'),
    ),
  },
  {
    value: 'popularity',
    label: 'Most popular',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.popularity, b.popularity, 'desc'),
    ),
  },
  {
    value: 'votes',
    label: 'Most voted',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.vote_count, b.vote_count, 'desc'),
    ),
  },
  {
    value: 'runtime',
    label: 'Longest runtime',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.runtime, b.runtime, 'desc'),
    ),
  },
  {
    value: 'runtime-short',
    label: 'Shortest runtime',
    compare: withTieBreak((a, b) =>
      compareNumbers(a.runtime, b.runtime, 'asc'),
    ),
  },
  {
    value: 'logged',
    label: 'Recently logged',
    compare: withTieBreak((a, b) =>
      compareNumbers(asTimestamp(a.logged_at), asTimestamp(b.logged_at), 'desc'),
    ),
  },
  {
    value: 'title',
    label: 'Title A–Z',
    compare: (a, b) =>
      titleCollator.compare(a.title || '', b.title || '') ||
      compareNumbers(a.release_year, b.release_year, 'desc'),
  },
  {
    value: 'title-desc',
    label: 'Title Z–A',
    compare: (a, b) =>
      titleCollator.compare(b.title || '', a.title || '') ||
      compareNumbers(a.release_year, b.release_year, 'desc'),
  },
]

const sortFilms = (films, sortBy) => {
  const option =
    SORT_OPTIONS.find((candidate) => candidate.value === sortBy) || SORT_OPTIONS[0]
  return [...films].sort(option.compare)
}

function ActorSkeleton() {
  return (
    <div>
      <Skeleton className={styles.skeletonBack} />
      <div className={styles.skeletonHeader}>
        <Skeleton className={styles.skeletonPortrait} ratio="3 / 4" />
        <div className={styles.skeletonCopy}>
          <Skeleton className={styles.skeletonTitle} />
          <Skeleton className={styles.skeletonLine} />
        </div>
      </div>
      <div className={styles.skeletonGrid}>
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className={styles.skeletonPoster} ratio="2 / 3" />
        ))}
      </div>
    </div>
  )
}

export default function ActorPage() {
  const { personId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [person, setPerson] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sortBy, setSortBy] = useState('newest')
  useTitle(person?.name || 'Person')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    api
      .getPerson(personId)
      .then((data) => {
        if (active) setPerson(data)
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
  }, [personId])

  const from = location.state?.from
  const backLabel = from?.startsWith('/film/') ? 'Back to the film' : 'Back to Discover'

  const goBack = () => {
    if (from) navigate(from, { state: location.state?.previous })
    else navigate('/')
  }

  if (loading && !person) return <ActorSkeleton />

  if (error) {
    return (
      <div>
        <button type="button" className={styles.back} onClick={goBack}>
          <ArrowLeft />
          {backLabel}
        </button>
        <EmptyState
          title="This person is missing."
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

  if (!person) return null

  const roles = person.roles.map((role) => role.label).join(' · ')
  const roleSummary = [roles, `${person.film_count} catalog films`]
    .filter(Boolean)
    .join(' · ')
  const sortedFilms = sortFilms(person.films, sortBy)

  return (
    <div>
      <button type="button" className={styles.back} onClick={goBack}>
        <ArrowLeft />
        {backLabel}
      </button>

      <header className={styles.header}>
        {person.profile_path ? (
          <img
            className={styles.portrait}
            src={person.profile_path}
            alt=""
            loading="eager"
            decoding="async"
          />
        ) : (
          <span className={styles.monogram} aria-hidden="true">
            {initials(person.name)}
          </span>
        )}
        <div className={styles.headerCopy}>
          <p className={styles.eyebrow}>Person</p>
          <h1 className={styles.title}>{person.name}</h1>
          <p className={styles.meta}>{roleSummary}</p>
        </div>
      </header>

      <section aria-labelledby="filmography-title">
        <SectionHeader
          id="filmography-title"
          title="Filmography"
          description="Every film in the archive with a credit for this person."
          action={`${person.film_count} films`}
        />
        <div className={styles.filmographyTools}>
          <label className={styles.sortLabel} htmlFor="filmography-sort">
            Sort
          </label>
          <span className={styles.sortControl}>
            <select
              id="filmography-sort"
              className={styles.sortSelect}
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value)}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className={styles.sortChevron} aria-hidden="true">
              ▾
            </span>
          </span>
        </div>
        <div className={styles.grid}>
          {sortedFilms.map((film) => (
            <Link
              key={film.tmdb_id}
              to={`/film/${film.tmdb_id}`}
              state={{ from: location.pathname, previous: location.state }}
              className={styles.credit}
            >
              <Poster movie={film} decorative />
              <span className={styles.creditTitle}>{film.title}</span>
              <span className={styles.creditMeta}>
                {[film.release_year, (film.role_labels || []).join(', ')]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              {film.user_rating ? (
                <span className={styles.creditRating}>
                  <StaticStars value={film.user_rating} size={11} />
                  <span className={styles.creditRatingValue} aria-hidden="true">
                    {Number(film.user_rating).toFixed(1)}
                  </span>
                </span>
              ) : null}
            </Link>
          ))}
        </div>
      </section>
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
