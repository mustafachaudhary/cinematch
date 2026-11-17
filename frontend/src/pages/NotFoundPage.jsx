import { Link } from 'react-router-dom'
import useTitle from '../hooks/useTitle'
import styles from './NotFoundPage.module.css'

export default function NotFoundPage() {
  useTitle('Not found')

  return (
    <div className={styles.page}>
      <p className={styles.code}>404</p>
      <h1 className={styles.title}>This print is missing.</h1>
      <p className={styles.body}>
        The address does not lead to a film, a page, or anything in the archive.
      </p>
      <Link to="/" className={styles.link}>
        Back to Discover
      </Link>
    </div>
  )
}
