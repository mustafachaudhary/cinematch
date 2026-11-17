import { useEffect, useRef, useState } from 'react'
import { api } from '../hooks/api'
import LogForm from './LogForm'
import Modal from './Modal'
import Poster from './Poster'
import styles from './LogFilmDialog.module.css'

export default function LogFilmDialog({ onClose, onLogged }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [selected, setSelected] = useState(null)
  const [loadingFilm, setLoadingFilm] = useState(false)
  const timer = useRef(null)

  useEffect(() => {
    clearTimeout(timer.current)
    const text = query.trim()
    if (text.length < 2) {
      setResults(null)
      setSearching(false)
      setSearchError('')
      return undefined
    }

    setSearching(true)
    setSearchError('')
    timer.current = setTimeout(async () => {
      try {
        const found = await api.tmdbSearch(text)
        setResults(found)
      } catch (err) {
        setSearchError(err.message)
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 350)

    return () => clearTimeout(timer.current)
  }, [query])

  const choose = async (film) => {
    setSelected(film)
    setLoadingFilm(true)
    try {
      const detail = await api.getMovie(film.tmdb_id, { match: false })
      setSelected(detail)
    } catch {
      /* the search result is enough to save a rating */
    } finally {
      setLoadingFilm(false)
    }
  }

  return (
    <Modal title="Log a film" onClose={onClose}>
      {!selected ? (
        <div>
          <label className={styles.label} htmlFor="film-search">
            Find a film on TMDB
          </label>
          <input
            id="film-search"
            className={styles.input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Start typing a title"
            autoComplete="off"
            data-autofocus
          />

          {searching ? <p className={styles.status}>Searching TMDB…</p> : null}
          {searchError ? (
            <p className={styles.error} role="alert">
              {searchError}
            </p>
          ) : null}

          {results ? (
            results.length ? (
              <ul className={styles.results}>
                {results.map((film) => (
                  <li key={film.tmdb_id}>
                    <button
                      type="button"
                      className={styles.result}
                      onClick={() => choose(film)}
                    >
                      <Poster movie={film} className={styles.resultPoster} decorative />
                      <span className={styles.resultCopy}>
                        <span className={styles.resultTitle}>{film.title}</span>
                        <span className={styles.resultMeta}>
                          {[film.release_year, (film.genres || []).slice(0, 2).join(', ')]
                            .filter(Boolean)
                            .join(' \u00b7 ')}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              !searching && (
                <p className={styles.status}>No films matched that title.</p>
              )
            )
          ) : (
            <p className={styles.hint}>
              Search the whole TMDB catalog. A film is added to this archive the first
              time you rate it.
            </p>
          )}
        </div>
      ) : (
        <div>
          <div className={styles.selected}>
            <Poster movie={selected} className={styles.selectedPoster} decorative />
            <div className={styles.selectedCopy}>
              <p className={styles.selectedTitle}>{selected.title}</p>
              <p className={styles.selectedMeta}>
                {[selected.release_year, selected.runtime ? `${selected.runtime} min` : null]
                  .filter(Boolean)
                  .join(' \u00b7 ')}
              </p>
            </div>
            <button
              type="button"
              className={styles.change}
              onClick={() => {
                setSelected(null)
                setLoadingFilm(false)
              }}
            >
              Choose another
            </button>
          </div>

          {loadingFilm ? (
            <p className={styles.status}>Opening the film record…</p>
          ) : (
            <LogForm
              movie={selected}
              initialRating={selected.user_rating || 0}
              initialReview={selected.user_review}
              onCancel={onClose}
              onSaved={() => {
                onLogged?.()
                onClose()
              }}
            />
          )}
        </div>
      )}
    </Modal>
  )
}
