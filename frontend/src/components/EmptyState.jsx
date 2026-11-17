import styles from './EmptyState.module.css'

export default function EmptyState({ title, children, action, className = '' }) {
  return (
    <div className={`${styles.root} ${className}`}>
      <h2 className={styles.title}>{title}</h2>
      {children ? <div className={styles.body}>{children}</div> : null}
      {action ? <div className={styles.action}>{action}</div> : null}
    </div>
  )
}
