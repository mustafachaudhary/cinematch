export const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function apiFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  const storedUser = localStorage.getItem("cinematch_username");
  const display = localStorage.getItem("cinematch_display_name");
  if (storedUser) headers["X-Username"] = storedUser;
  if (display) headers["X-Display-Name"] = display;

  const res = await fetch(`${BASE}${path}`, { headers, ...options });
  if (!res.ok) {
    let detail = `API error ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function apiUpload(path, formData) {
  // No Content-Type header — the browser sets the multipart boundary.
  const headers = {};
  const storedUser = localStorage.getItem("cinematch_username");
  if (storedUser) headers["X-Username"] = storedUser;

  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body: formData });
  if (!res.ok) {
    let detail = `API error ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* keep generic message */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  getFeed: (limit = 16, exclude = []) => {
    const skipped = exclude.filter(Boolean).slice(0, 60)
    const suffix = skipped.length ? `&exclude=${skipped.join(",")}` : ""
    return apiFetch(`/movies/feed?limit=${limit}${suffix}`)
  },
  search: (q, limit = 16) =>
    apiFetch(`/movies/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  getPerson: (personId) => apiFetch(`/people/${personId}`),
  tmdbSearch: (q) => apiFetch(`/movies/tmdb-search?q=${encodeURIComponent(q)}`),
  engineStatus: () => apiFetch("/movies/engine-status"),
  profileSummary: () => apiFetch("/movies/profile-summary"),
  getMovie: (tmdbId, { match = true } = {}) =>
    apiFetch(`/movies/${tmdbId}${match ? "" : "?include_match=false"}`),

  setFeedback: (tmdbId, kind = "not_interested") =>
    apiFetch(`/feedback/${tmdbId}`, {
      method: "POST",
      body: JSON.stringify({ kind }),
    }),
  clearFeedback: (tmdbId, kind) =>
    apiFetch(`/feedback/${tmdbId}${kind ? `?kind=${kind}` : ""}`, {
      method: "DELETE",
    }),
  getFeedback: () => apiFetch("/feedback"),
  recordImpressions: (tmdbIds, sessionId) =>
    apiFetch("/feedback/impressions", {
      method: "POST",
      body: JSON.stringify({ tmdb_ids: tmdbIds, session_id: sessionId }),
    }),

  getWatchlist: () => apiFetch("/watchlist/"),
  addToWatchlist: (tmdbId) =>
    apiFetch(`/watchlist/${tmdbId}`, { method: "POST" }),
  removeFromWatchlist: (tmdbId) =>
    apiFetch(`/watchlist/${tmdbId}`, { method: "DELETE" }),

  getLogs: () => apiFetch("/logs/"),
  logMovie: (tmdb_id, rating) =>
    apiFetch("/logs/", { method: "POST", body: JSON.stringify({ tmdb_id, rating }) }),
  removeLog: (tmdb_id) => apiFetch(`/logs/${tmdb_id}`, { method: "DELETE" }),

  addReview: (tmdb_id, content) =>
    apiFetch(`/reviews/${tmdb_id}`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  scoreReview: (content) =>
    apiFetch("/reviews/score", { method: "POST", body: JSON.stringify({ content }) }),
  deleteReview: (tmdb_id) =>
    apiFetch(`/reviews/${tmdb_id}`, { method: "DELETE" }),
  myReviews: () => apiFetch("/reviews/"),

  getProfile: () => apiFetch("/profile/"),
  updateProfile: (data) =>
    apiFetch("/profile/", { method: "PUT", body: JSON.stringify(data) }),
  checkUser: (username) =>
    apiFetch(`/profile/exists?username=${encodeURIComponent(username)}`),
  signup: (data) => apiFetch("/profile/signup", { method: "POST", body: JSON.stringify(data) }),
  login: (data) => apiFetch("/profile/login", { method: "POST", body: JSON.stringify(data) }),

  uploadPfp: (formData) => apiUpload("/profile/upload-pfp", formData),
  importLetterboxd: (formData) => apiUpload("/profile/import-letterboxd", formData),
  signout: () => apiFetch("/profile/signout", { method: "POST" }),
};
