const BASE = "http://localhost:8000"

export async function apiFetch(path, options = {}) {
  // Inject username header if available in localStorage
  const headers = { "Content-Type": "application/json" };
  const storedUser = localStorage.getItem('cinematch_username')
  const storedDisplay = localStorage.getItem('cinematch_display_name')
  if (storedUser) headers['X-Username'] = storedUser
  if (storedDisplay) headers['X-Display-Name'] = storedDisplay

  const res = await fetch(`${BASE}${path}`, {
    headers,
    ...options,
  })
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export const api = {
  getFeed: (limit = 16) => apiFetch(`/movies/feed?limit=${limit}`),
  getProfileSummary: () => apiFetch("/movies/profile-summary"),
  search: (q, limit = 16) => apiFetch(`/movies/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  tmdbSearch: (q) => apiFetch(`/movies/tmdb-search?q=${encodeURIComponent(q)}`),
  getLogs: () => apiFetch("/logs/"),
  logMovie: (tmdb_id, rating) =>
    apiFetch("/logs/", { method: "POST", body: JSON.stringify({ tmdb_id, rating }) }),
  removeLog: (tmdb_id) =>
    apiFetch(`/logs/${tmdb_id}`, { method: "DELETE" }),
  
  // Profile endpoints
  getProfile: () => apiFetch("/profile/"),
  updateProfile: (data) =>
    apiFetch("/profile/", { method: "PUT", body: JSON.stringify(data) }),
  checkUser: (username) => apiFetch(`/profile/exists?username=${encodeURIComponent(username)}`),
  signup: (data) => apiFetch("/profile/signup", { method: "POST", body: JSON.stringify(data) }),
  login: (data) => apiFetch("/profile/login", { method: "POST", body: JSON.stringify(data) }),
}

