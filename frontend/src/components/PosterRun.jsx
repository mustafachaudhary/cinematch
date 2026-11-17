import { Link, useLocation } from 'react-router-dom'
import Poster from './Poster'
import { formatScore } from '../lib/format'
import styles from './PosterRun.module.css'

function defaultFact(film) {
  const score = formatScore(film.score)
  if (score) return `${score} match`
  return film.release_year || film.year || ''
}

export default function PosterRun({
  films = [],
  liftedIndex = 0,
  onSelect,
  fact = defaultFact,
  ariaLabel = 'Films',
  className = '',
}) {
  const location = useLocation()

  if (!films.length) return null

  return (
    <div className={`${styles.rail} ${className}`} aria-label={ariaLabel}>
      {films.map((film, index) => {
        const lifted = index === liftedIndex
        const key = `${film.tmdb_id}-${index}`
        const content = (
          <>
            <Poster movie={film} className={styles.poster} decorative />
            <span className={styles.caption}>
              <span className={styles.title}>{film.title}</span>
              <span className={styles.fact}>{fact(film)}</span>
            </span>
          </>
        )

        if (onSelect) {
          return (
            <button
              key={key}
              type="button"
              className={styles.item}
              data-lifted={lifted ? 'true' : undefined}
              onClick={() => onSelect(film)}
              aria-label={`Choose ${film.title}`}
            >
              {content}
            </button>
          )
        }

        return (
          <Link
            key={key}
            to={`/film/${film.tmdb_id}`}
            state={{ from: `${location.pathname}${location.search}` }}
            className={styles.item}
            data-lifted={lifted ? 'true' : undefined}
          >
            {content}
          </Link>
        )
      })}
    </div>
  )
}
