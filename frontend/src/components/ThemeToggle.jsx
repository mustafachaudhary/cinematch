import { useTheme } from '../context/ThemeProvider'
import styles from './ThemeToggle.module.css'

export default function ThemeToggle({ className = '' }) {
  const { theme, toggleTheme } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'

  return (
    <button
      type="button"
      className={`${styles.toggle} ${className}`}
      onClick={toggleTheme}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      <span className={theme === 'light' ? styles.active : styles.idle}>Light</span>
      <span className={styles.separator} aria-hidden="true">
        /
      </span>
      <span className={theme === 'dark' ? styles.active : styles.idle}>Dark</span>
    </button>
  )
}
