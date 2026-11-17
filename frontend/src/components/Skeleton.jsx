import styles from './Skeleton.module.css'

export default function Skeleton({ className = '', style, ratio }) {
  return (
    <span
      className={`${styles.block} ${className}`}
      style={{ ...(ratio ? { aspectRatio: ratio } : null), ...style }}
      aria-hidden="true"
    />
  )
}
