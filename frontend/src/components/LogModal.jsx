import LogForm from './LogForm'
import Modal from './Modal'
import Poster from './Poster'
import styles from './LogModal.module.css'

export default function LogModal({ movie, onClose, onSaved }) {
  return (
    <Modal title="Log a film" onClose={onClose}>
      <div className={styles.film}>
        <Poster movie={movie} className={styles.poster} decorative />
        <div>
          <p className={styles.title}>{movie.title}</p>
          <p className={styles.meta}>
            {[movie.release_year, movie.runtime ? `${movie.runtime} min` : null]
              .filter(Boolean)
              .join(' \u00b7 ')}
          </p>
        </div>
      </div>
      <LogForm
        movie={movie}
        initialRating={movie.user_rating || 0}
        initialReview={movie.user_review}
        onCancel={onClose}
        onSaved={onSaved}
      />
    </Modal>
  )
}
