import styles from './PageHeader.module.css'

export default function PageHeader({ title, intro, action, className = '' }) {
  return (
    <header className={`${styles.header} ${className}`}>
      <div className={styles.copy}>
        <h1 className={styles.title}>{title}</h1>
        {intro ? <p className={styles.intro}>{intro}</p> : null}
      </div>
      {action ? <div className={styles.action}>{action}</div> : null}
    </header>
  )
}
