import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthProvider'

export function ProtectedRoute() {
  const { user } = useAuth()
  const location = useLocation()

  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    )
  }
  return <Outlet />
}

export function GuestRoute() {
  const { user } = useAuth()
  if (user) return <Navigate to="/" replace />
  return <Outlet />
}
