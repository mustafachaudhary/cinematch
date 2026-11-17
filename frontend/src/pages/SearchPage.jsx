import { useEffect, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { SHOWCASE } from '../data/showcase'
import { formatCount, plainReason } from '../lib/format'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import PageHeader from '../components/PageHeader'
import Poster from '../components/Poster'
import PosterRun from '../components/PosterRun'
import SectionHeader from '../components/SectionHeader'
import Skeleton from '../components/Skeleton'
import styles from './SearchPage.module.css'

const HINTS = [
  'something like Interstellar',
  'I want to cry',
  'feel-good comedy',
  'mind-bending thriller',
  'feeling nostalgic',
]

function ResultSkeleton() {
  return (
    <div className={styles.resultSkeleton}>
      <Skeleton className={styles.skeletonPoster} ratio="2 / 3" />
      <div className={styles.skeletonCopy}>
        <Skeleton className={styles.skeletonTitle} />
        <Skeleton className={styles.skeletonLine} />
        <Skeleton className={styles.skeletonLineShort} />
      </div>
      <Skeleton className={styles.skeletonScore} />
    </div>
  )
}

function ResultCard({ movie, from, exact = false }) {
  return (
    <Link
      to={`/film/${movie.tmdb_id}`}
      state={{ from }}
      className={`${styles.result} ${exact ? styles.exactResult : ''}`}
    >
      <Poster movie={movie} className={styles.poster} decorative />
      <span className={styles.resultBody}>
        <span className={`${styles.resultTitle} ${exact ? styles.exactTitle : ''}`}>
          {movie.title}
        </span>
        <span className={styles.resultMeta}>
          {[movie.release_year, (movie.genres || []).slice(0, 2).join(', ')]
            .filter(Boolean)
            .join(' \u00b7 ')}
        </span>
        {exact && movie.overview ? (
          <span className={styles.exactOverview}>{movie.overview}</span>
        ) : null}
        <span className={styles.reason}>{plainReason(movie.reason)}</span>
      </span>
      <span className={styles.score}>
        {exact ? (
          <span className={styles.exactTag}>Exact</span>
        ) : (
          <>
            {movie.score != null ? Math.round(movie.score) : '—'}
            <span className={styles.scoreMark}>%</span>
          </>
        )}
      </span>
    </Link>
  )
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const q = params.get('q') || ''
  const [input, setInput] = useState(q)
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [retry, setRetry] = useState(0)
  useTitle(q ? `Search: ${q}` : 'Search')

  useEffect(() => {
    setInput(q)
  }, [q])

  useEffect(() => {
    const text = q.trim()
    if (!text) {
      setResults(null)
      setLoading(false)
      setError(null)
      return undefined
    }

    let active = true
    setLoading(true)
    setError(null)
    api
      .search(text)
      .then((data) => {
        if (active) setResults(data)
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
  }, [q, retry])

  const runSearch = (value) => {
    const text = value.trim()
    if (!text) {
      setParams({})
      return
    }
    setInput(text)
    setParams({ q: text })
  }

  const submit = (event) => {
    event.preventDefault()
    runSearch(input)
  }

  const exact = results?.exact || null
  const exactId = exact?.tmdb_id
  const similar = (results?.results || []).filter(
    (movie) => !exactId || movie.tmdb_id !== exactId,
  )
  const from = `${location.pathname}${location.search}`
  const resultCount = similar.length + (exact ? 1 : 0)

  return (
    <div>
      <PageHeader
        title="Search the archive"
        intro="Ask by title, by a film you love, or by a feeling. Every synopsis in the catalog is a candidate."
      />

      <form className={styles.search} onSubmit={submit}>
        <div className={styles.searchCopy}>
          <label className={styles.searchLabel} htmlFor="archive-search">
            Title, feeling, or a film you love
          </label>
          <input
            id="archive-search"
            className={styles.input}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Try “something like In the Mood for Love”"
            autoComplete="off"
            autoFocus={!q}
          />
        </div>
        <Button type="submit" className={styles.submit}>
          Search
        </Button>
      </form>

      <div className={styles.hints}>
        <span className={styles.hintsLabel}>Try</span>
        {HINTS.map((hint) => (
          <button
            key={hint}
            type="button"
            className={styles.hint}
            onClick={() => runSearch(hint)}
          >
            {hint}
          </button>
        ))}
      </div>

      {!q.trim() ? (
        <section className={styles.browse} aria-labelledby="start-with-a-film">
          <SectionHeader
            title="Start with a film you love"
            description="Each print runs a search for films with a similar shape."
          />
          <PosterRun
            films={SHOWCASE.slice(0, 7)}
            liftedIndex={2}
            onSelect={(film) => runSearch(`something like ${film.title}`)}
            fact={() => 'Find similar'}
            ariaLabel="Search from a film you love"
          />
        </section>
      ) : null}

      {q.trim() ? (
        <section className={styles.resultsSection} aria-labelledby="search-results">
          <SectionHeader
            title="Results"
            action={results ? `${formatCount(resultCount)} films` : undefined}
          />
          {results?.score_label ? (
            <p className={styles.mode}>
              {results.mode === 'anchor' && results.reference_title
                ? `Because you asked for films like ${results.reference_title}.`
                : results.score_label}
            </p>
          ) : null}

          {loading ? (
            <div className={styles.results}>
              {Array.from({ length: 6 }, (_, index) => (
                <ResultSkeleton key={index} />
              ))}
            </div>
          ) : error ? (
            <EmptyState
              title="The search did not load."
              action={
                <Button variant="secondary" onClick={() => setRetry((n) => n + 1)}>
                  Try again
                </Button>
              }
            >
              {error}
            </EmptyState>
          ) : exact || similar.length ? (
            <>
              {exact ? (
                <div className={styles.exactBlock}>
                  <p className={styles.exactLabel}>The film you asked for</p>
                  <ResultCard movie={exact} from={from} exact />
                </div>
              ) : null}

              {similar.length ? (
                <>
                  {exact ? (
                    <SectionHeader
                      title="More like this"
                      description="Semantic matches, with franchise and credit evidence first."
                    />
                  ) : null}
                  <ul className={styles.results}>
                    {similar.map((movie) => (
                      <li key={movie.tmdb_id}>
                        <ResultCard movie={movie} from={from} />
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </>
          ) : (
            <EmptyState title="Nothing matched that phrase.">
              Try a film title, a looser mood, or one of the prompts above. The engine
              searches meaning across the whole synopsis.
            </EmptyState>
          )}
        </section>
      ) : null}
    </div>
  )
}
