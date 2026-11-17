import { Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import ScrollToTop from './components/ScrollToTop'
import { GuestRoute, ProtectedRoute } from './components/RouteGuards'
import ActorPage from './pages/ActorPage'
import HomeFeed from './pages/HomeFeed'
import LoginPage from './pages/LoginPage'
import LogsPage from './pages/LogsPage'
import MoviePage from './pages/MoviePage'
import NotFoundPage from './pages/NotFoundPage'
import ProfilePage from './pages/ProfilePage'
import SearchPage from './pages/SearchPage'
import WatchlistPage from './pages/WatchlistPage'

export default function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route element={<GuestRoute />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route index element={<HomeFeed />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="films" element={<LogsPage />} />
            <Route path="watchlist" element={<WatchlistPage />} />
            <Route path="profile" element={<ProfilePage />} />
            <Route path="film/:tmdbId" element={<MoviePage />} />
            <Route path="actor/:personId" element={<ActorPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </>
  )
}
