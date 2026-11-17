import styles from './Poster.module.css'

export default function Poster({
  movie,
  src,
  title,
  ratio = 'poster',
  decorative = false,
  className = '',
  loading = 'lazy',
}) {
  const image = src || movie?.poster_path
  const name = title || movie?.title || 'Film poster'
  const year = movie?.release_year || movie?.year

  return (
    <span className={`${styles.frame} ${className}`} data-ratio={ratio}>
      {image ? (
        <img
          className={styles.image}
          src={image}
          alt={decorative ? '' : name}
          loading={loading}
          decoding="async"
        />
      ) : (
        <span className={styles.fallback}>
          <span className={styles.fallbackTitle}>{name}</span>
          {year ? <span className={styles.fallbackYear}>{year}</span> : null}
        </span>
      )}
    </span>
  )
}
