import { useState } from 'react'
import styles from './Stars.module.css'

const STAR_PATH =
  'M12 2.4l2.95 5.98 6.6.96-4.78 4.66 1.13 6.57L12 17.46l-5.9 3.1 1.13-6.57L2.45 9.34l6.6-.96L12 2.4z'

function Star({ fill = 0, size = 15 }) {
  const pct = Math.max(0, Math.min(1, fill)) * 100
  return (
    <span
      className={styles.star}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg className={styles.base} viewBox="0 0 24 24" width={size} height={size}>
        <path d={STAR_PATH} />
      </svg>
      <span className={styles.clip} style={{ width: `${pct}%` }}>
        <svg className={styles.fill} viewBox="0 0 24 24" width={size} height={size}>
          <path d={STAR_PATH} />
        </svg>
      </span>
    </span>
  )
}

const fills = (value) =>
  [1, 2, 3, 4, 5].map((step) => {
    if (value >= step - 0.25) return 1
    if (value >= step - 0.75) return 0.5
    return 0
  })

export function StaticStars({ value = 0, size = 15, showValue = false, className = '' }) {
  const numeric = Number(value)
  return (
    <span
      className={`${styles.static} ${className}`}
      role="img"
      aria-label={`${numeric.toFixed(1)} out of 5 stars`}
    >
      {fills(numeric).map((fill, index) => (
        <Star key={index} fill={fill} size={size} />
      ))}
      {showValue ? <span className={styles.value}>{numeric.toFixed(1)}</span> : null}
    </span>
  )
}

export default function StarInput({ value = 0, onChange, className = '' }) {
  const [hover, setHover] = useState(null)
  const shown = hover ?? value ?? 0

  const pick = (event, step) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const half = event.clientX - rect.left < rect.width / 2
    setHover(null)
    onChange(half ? step - 0.5 : step)
  }

  return (
    <span
      className={`${styles.picker} ${className}`}
      role="group"
      aria-label="Your rating out of five stars"
    >
      {[1, 2, 3, 4, 5].map((step) => (
        <button
          key={step}
          type="button"
          className={styles.starButton}
          aria-label={`Rate ${step} out of 5`}
          onMouseMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect()
            setHover(event.clientX - rect.left < rect.width / 2 ? step - 0.5 : step)
          }}
          onMouseLeave={() => setHover(null)}
          onClick={(event) => pick(event, step)}
        >
          <Star
            fill={shown >= step - 0.25 ? 1 : shown >= step - 0.75 ? 0.5 : 0}
            size={28}
          />
        </button>
      ))}
      <span className={styles.readout} aria-live="polite">
        {(value ?? 0).toFixed(1)}
        <span className={styles.scale}> / 5</span>
      </span>
    </span>
  )
}
