import { useState } from 'react'
import { api } from '../hooks/api'
import Button from './Button'
import Modal from './Modal'
import Poster from './Poster'
import { useToast } from './Toast'
import styles from './FavoritePicker.module.css'

export default function FavoritePicker({ films, slot, onClose, onPicked }) {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [savingId, setSavingId] = useState(null)

  const needle = query.trim().toLowerCase()
  const filtered = films.filter((film) => {
    if (!needle) return true
    return [film.title, film.release_year, ...(film.genres || [])]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(needle)
  })
  const shown = filtered.slice(0, 60)

  const choose = async (film) => {
    setSavingId(film.tmdb_id)
    try {
      await api.updateProfile({ [`favorite_movie_${slot}`]: film.tmdb_id })
      onPicked?.(slot, film)
      toast(`Favorite ${slot} is now “${film.title}”.`)
      onClose()
    } catch (err) {
      toast(err.message, 'error')
    } finally {
      setSavingId(null)
    }
  }

  return (
    <Modal title={`Choose favorite ${slot}`} onClose={onClose}>
      {films.length ? (
        <>
          <label className={styles.srOnly} htmlFor="favorite-filter">
            Filter your archive
          </label>
          <input
            id="favorite-filter"
            className={styles.input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter your archive"
            autoComplete="off"
            data-autofocus
          />
          <p className={styles.count}>
            {filtered.length === films.length
              ? `${films.length} films in the archive`
              : `${filtered.length} of ${films.length} films`}
          </p>

          {shown.length ? (
            <ul className={styles.list}>
              {shown.map((film) => (
                <li key={film.tmdb_id}>
                  <button
                    type="button"
                    className={styles.row}
                    onClick={() => choose(film)}
                    disabled={savingId !== null}
                  >
                    <Poster movie={film} className={styles.poster} decorative />
                    <span className={styles.copy}>
                      <span className={styles.title}>{film.title}</span>
                      <span className={styles.meta}>
                        {[film.release_year, (film.genres || []).slice(0, 2).join(', ')]
                          .filter(Boolean)
                          .join(' \u00b7 ')}
                      </span>
                    </span>
                    <span className={styles.action}>
                      {savingId === film.tmdb_id ? 'Saving…' : 'Choose'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.empty}>Nothing in the archive matches that filter.</p>
          )}
        </>
      ) : (
        <div>
          <p className={styles.empty}>
            Your archive is empty. Log a film first, then pin it here.
          </p>
          <div className={styles.actions}>
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
