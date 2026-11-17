import { useEffect, useState } from 'react'
import { api } from '../hooks/api'
import { sentimentTone } from '../lib/format'
import Button from './Button'
import StarInput from './Stars'
import { useToast } from './Toast'
import styles from './LogForm.module.css'

const reviewText = (review) => {
  if (!review) return ''
  return typeof review === 'string' ? review : review.content || ''
}

export default function LogForm({
  movie,
  initialRating = 0,
  initialReview = null,
  onSaved,
  onCancel,
}) {
  const toast = useToast()
  const startingReview = reviewText(initialReview)
  const [rating, setRating] = useState(Number(initialRating) || 0)
  const [review, setReview] = useState(startingReview)
  const [vibe, setVibe] = useState(null)
  const [scoring, setScoring] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const text = review.trim()
    if (text.length < 3) {
      setVibe(null)
      setScoring(false)
      return undefined
    }
    setScoring(true)
    const timer = setTimeout(async () => {
      try {
        const result = await api.scoreReview(text)
        setVibe(result)
      } catch {
        setVibe(null)
      } finally {
        setScoring(false)
      }
    }, 450)
    return () => clearTimeout(timer)
  }, [review])

  const submit = async (event) => {
    event.preventDefault()
    setError('')

    if (!rating) {
      setError('Choose a rating before saving.')
      return
    }
    const text = review.trim()
    if (text.length > 0 && text.length < 3) {
      setError('A review needs at least three characters.')
      return
    }

    setSaving(true)
    try {
      await api.logMovie(movie.tmdb_id, rating)

      let reviewError = null
      if (text.length >= 3) {
        try {
          await api.addReview(movie.tmdb_id, text)
        } catch (err) {
          reviewError = err.message
        }
      } else if (startingReview) {
        try {
          await api.deleteReview(movie.tmdb_id)
        } catch {
          /* the rating is already saved */
        }
      }

      if (reviewError) {
        toast(`Rating saved. Review failed: ${reviewError}`, 'error')
      } else {
        toast(`Logged “${movie.title}” at ${rating.toFixed(1)} / 5.`)
      }
      onSaved?.()
    } catch (err) {
      setError(err.message)
      toast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const compound = Number(vibe?.compound)
  const marker = Number.isFinite(compound) ? ((compound + 1) / 2) * 100 : 50
  const tone = Number.isFinite(compound) ? sentimentTone(compound) : 'mixed'

  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.ratingBlock}>
        <span className={styles.label}>Your rating</span>
        <StarInput value={rating} onChange={setRating} />
      </div>

      <label className={styles.reviewBlock} htmlFor="log-review">
        <span className={styles.label}>A note, if you want one</span>
        <textarea
          id="log-review"
          className={styles.textarea}
          value={review}
          onChange={(event) => setReview(event.target.value)}
          rows={4}
          maxLength={2000}
          placeholder="What stayed with you?"
        />
      </label>

      <div className={styles.vibe} aria-live="polite">
        <div className={styles.vibeHead}>
          <span className={styles.vibeLabel}>
            {scoring ? 'Reading the note…' : vibe ? `Vibe: ${vibe.label}` : 'Vibe'}
          </span>
          <span className={styles.vibeScale}>negative / positive</span>
        </div>
        <div className={styles.vibeTrack} data-tone={tone}>
          <span className={styles.vibeMarker} style={{ left: `${marker}%` }} />
        </div>
      </div>

      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}

      <div className={styles.actions}>
        {onCancel ? (
          <Button variant="quiet" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        ) : null}
        <Button type="submit" disabled={saving || !rating}>
          {saving ? 'Saving…' : initialRating ? 'Update log' : 'Save to archive'}
        </Button>
      </div>
    </form>
  )
}
