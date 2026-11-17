import { Link } from 'react-router-dom'
import styles from './Brand.module.css'

export function BrandMark({ className = '' }) {
  return (
    <svg
      className={`${styles.mark} ${className}`}
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      aria-hidden="true"
    >
      <rect x="2.8" y="4.2" width="18.4" height="15.6" stroke="currentColor" strokeWidth="1.7" />
      <path d="M7.2 4.2v15.6M16.8 4.2v15.6" stroke="currentColor" strokeWidth="1.7" />
      <rect x="9.8" y="8.2" width="4.4" height="7.6" fill="currentColor" />
    </svg>
  )
}

export default function Brand({ to = '/', tagline = '', className = '' }) {
  return (
    <Link to={to} className={`${styles.brand} ${className}`} aria-label="CineMatch home">
      <BrandMark />
      <span className={styles.copy}>
        <span className={styles.name}>CineMatch</span>
        {tagline ? <span className={styles.tagline}>{tagline}</span> : null}
      </span>
    </Link>
  )
}
