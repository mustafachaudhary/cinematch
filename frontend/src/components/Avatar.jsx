import { initials } from '../lib/format'
import styles from './Avatar.module.css'

export default function Avatar({ name, src, size = 'sm', className = '' }) {
  return (
    <span
      className={`${styles.avatar} ${className}`}
      data-size={size}
      aria-hidden={src ? undefined : 'true'}
    >
      {src ? (
        <img className={styles.image} src={src} alt={name || 'Profile photo'} />
      ) : (
        <span className={styles.initial}>{initials(name)}</span>
      )}
    </span>
  )
}
