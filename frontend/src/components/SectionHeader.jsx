import styles from './SectionHeader.module.css'

export default function SectionHeader({ id, title, description, action, className = '' }) {
  return (
    <div className={`${styles.header} ${className}`}>
      <div className={styles.copy}>
        <h2 id={id} className={styles.title}>
          {title}
        </h2>
        {description ? <p className={styles.description}>{description}</p> : null}
      </div>
      {action ? <div className={styles.action}>{action}</div> : null}
    </div>
  )
}
