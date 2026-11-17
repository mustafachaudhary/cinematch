import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'
import Avatar from './Avatar'
import Brand from './Brand'
import ThemeToggle from './ThemeToggle'
import styles from './AppShell.module.css'

const NAV = [
  { to: '/', label: 'Discover', end: true },
  { to: '/search', label: 'Search' },
  { to: '/films', label: 'My Films' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/profile', label: 'Profile' },
]

function sectionLabel(pathname) {
  if (pathname === '/') return 'Discover'
  if (pathname.startsWith('/search')) return 'Search'
  if (pathname.startsWith('/films')) return 'The archive'
  if (pathname.startsWith('/watchlist')) return 'Watchlist'
  if (pathname.startsWith('/profile')) return 'Profile'
  if (pathname.startsWith('/film/')) return 'Film'
  if (pathname.startsWith('/actor/')) return 'Person'
  return 'CineMatch'
}

export default function AppShell() {
  const { user, displayName, signOut } = useAuth()
  const location = useLocation()
  const section = sectionLabel(location.pathname)

  return (
    <div className={styles.shell}>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>

      <aside className={styles.sidebar}>
        <div className={styles.brandBlock}>
          <Brand tagline="Private film archive" />
        </div>

        <nav className={styles.nav} aria-label="Primary">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `${styles.navLink} ${isActive ? styles.navActive : ''}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className={styles.sidebarFoot}>
          <NavLink to="/profile" className={styles.member} aria-label="Open your profile">
            <Avatar name={displayName || user} size="sm" />
            <span className={styles.memberCopy}>
              <span className={styles.memberName}>{displayName || `@${user}`}</span>
              <span className={styles.memberHandle}>
                {displayName ? `@${user}` : 'Member'}
              </span>
            </span>
          </NavLink>
          <button type="button" className={styles.signOut} onClick={signOut}>
            Sign out
          </button>
        </div>
      </aside>

      <header className={styles.topbar}>
        <div className={styles.topbarRow}>
          <Brand className={styles.mobileBrand} />
          <span className={styles.section}>{section}</span>
          <ThemeToggle />
        </div>

        <nav className={styles.topNav} aria-label="Sections">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `${styles.topNavLink} ${isActive ? styles.topNavActive : ''}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>

      <div className={styles.sheet}>
        <main id="main" className={styles.main}>
          <Outlet />
          <p className={styles.creditLine}>Film data &amp; artwork from TMDB.</p>
        </main>
      </div>

    </div>
  )
}
