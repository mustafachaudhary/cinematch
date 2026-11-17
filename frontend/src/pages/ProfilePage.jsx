import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { BASE, api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { formatCount, formatDate } from '../lib/format'
import Avatar from '../components/Avatar'
import Button from '../components/Button'
import EditProfileModal from '../components/EditProfileModal'
import EmptyState from '../components/EmptyState'
import FavoritePicker from '../components/FavoritePicker'
import ImportDialog from '../components/ImportDialog'
import PageHeader from '../components/PageHeader'
import Poster from '../components/Poster'
import SectionHeader from '../components/SectionHeader'
import Skeleton from '../components/Skeleton'
import { StaticStars } from '../components/Stars'
import { useToast } from '../components/Toast'
import { useAuth } from '../context/AuthProvider'
import styles from './ProfilePage.module.css'

const RATING_STEPS = [0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]

function ProfileSkeleton() {
  return (
    <div>
      <h1 className={styles.srOnly}>Profile</h1>
      <div className={styles.header}>
        <Skeleton className={styles.skeletonPortrait} ratio="4 / 5" />
        <div className={styles.skeletonIdentity}>
          <Skeleton className={styles.skeletonName} />
          <Skeleton className={styles.skeletonLine} />
          <Skeleton className={styles.skeletonLineShort} />
        </div>
      </div>
      <div className={styles.skeletonStats}>
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className={styles.skeletonStat} />
        ))}
      </div>
    </div>
  )
}

export default function ProfilePage() {
  const toast = useToast()
  const navigate = useNavigate()
  const { signOut, signIn } = useAuth()
  const [profile, setProfile] = useState(null)
  const [logs, setLogs] = useState(null)
  const [taste, setTaste] = useState(null)
  const [engine, setEngine] = useState(null)
  const [reviews, setReviews] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editOpen, setEditOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [pickerSlot, setPickerSlot] = useState(null)
  const [pfpUploading, setPfpUploading] = useState(false)
  useTitle('Profile')

  const load = async () => {
    setLoading(true)
    const [profileRes, logsRes, tasteRes, engineRes, reviewsRes] = await Promise.allSettled([
      api.getProfile(),
      api.getLogs(),
      api.profileSummary(),
      api.engineStatus(),
      api.myReviews(),
    ])
    if (profileRes.status === 'fulfilled') {
      setProfile(profileRes.value)
      setError(null)
    } else {
      setError(profileRes.reason?.message || 'Profile could not be loaded.')
    }
    if (logsRes.status === 'fulfilled') setLogs(logsRes.value)
    if (tasteRes.status === 'fulfilled') setTaste(tasteRes.value)
    if (engineRes.status === 'fulfilled') setEngine(engineRes.value)
    if (reviewsRes.status === 'fulfilled') setReviews(reviewsRes.value)
    setLoading(false)
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSignOut = async () => {
    await signOut()
    navigate('/login')
  }

  const uploadPfp = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    setPfpUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const result = await api.uploadPfp(formData)
      setProfile((current) => ({
        ...current,
        pfp_path: result.pfp_path || current.pfp_path,
      }))
      toast('Profile photo updated.')
    } catch (err) {
      toast(err.message, 'error')
    } finally {
      setPfpUploading(false)
      event.target.value = ''
    }
  }

  if (loading && !profile) return <ProfileSkeleton />

  if (error) {
    return (
      <div>
        <PageHeader title="Profile" />
        <EmptyState
          title="Your profile could not be opened."
          action={
            <Button variant="secondary" onClick={load}>
              Try again
            </Button>
          }
        >
          {error}
        </EmptyState>
      </div>
    )
  }

  if (!profile) return null

  const pfpSrc = profile.pfp_path
    ? profile.pfp_path.startsWith('http')
      ? profile.pfp_path
      : `${BASE}${profile.pfp_path}`
    : null
  const displayName = profile.display_name || profile.username
  const ratedCount = profile.stats?.rated_count || 0
  const reviewCount = profile.stats?.reviews_count || 0
  const average = logs?.length
    ? logs.reduce((sum, log) => sum + Number(log.rating || 0), 0) / logs.length
    : null
  const hearted = (logs || []).filter((log) => log.rating >= 4.5)
  const heartedTop = hearted.slice(0, 6)
  const reviewList = reviews?.reviews || []
  const loved = taste?.genres?.loved || []
  const avoided = taste?.genres?.avoided || []
  const maxGenre = loved.length ? Math.max(...loved.map(([, value]) => Number(value))) : 0
  const distribution = taste?.sentiment?.distribution || {}
  const sentimentTotal =
    (distribution.positive || 0) + (distribution.mixed || 0) + (distribution.negative || 0)
  const segment = (value) =>
    sentimentTotal ? `${(Number(value || 0) / sentimentTotal) * 100}%` : '0%'

  const favoriteSlots = [1, 2, 3, 4].map((slot) => {
    const movieId = profile[`favorite_movie_${slot}`]
    if (!movieId) return { slot, movie: null }
    const summary = (profile.favorites || []).find(
      (film) => Number(film.tmdb_id) === Number(movieId),
    )
    const log = (logs || []).find((film) => Number(film.tmdb_id) === Number(movieId))
    const movie = summary ? { ...summary, rating: log?.rating } : log
    return { slot, movie: movie || null }
  })

  const ratingDistribution = RATING_STEPS.map((value) => ({
    value,
    count: (logs || []).filter((log) => Number(log.rating) === value).length,
  }))
  const maxRatingCount = Math.max(...ratingDistribution.map((entry) => entry.count), 1)
  const distributionLabel = ratingDistribution
    .map((entry) => `${entry.value}: ${entry.count}`)
    .join(', ')

  const engineRows = engine
    ? [
        ['Catalog', `${formatCount(engine.catalog_size)} films`],
        ['Embedded', formatCount(engine.embedded_movies)],
        [
          'Ratings in matrix',
          formatCount(engine.collaborative_filtering?.total_ratings),
        ],
        ['Raters', formatCount(engine.collaborative_filtering?.users_with_ratings)],
        ['Sentiment', engine.sentiment_model?.split(' (')[0] || 'VADER'],
      ]
    : []

  const saveProfile = (next) => {
    setProfile(next)
    signIn({ username: next.username, display_name: next.display_name || '' })
  }

  const pickFavorite = (slot, film) => {
    setProfile((current) => {
      const others = (current.favorites || []).filter(
        (item) => Number(item.tmdb_id) !== Number(film.tmdb_id),
      )
      return {
        ...current,
        [`favorite_movie_${slot}`]: film.tmdb_id,
        favorites: [...others, film],
      }
    })
  }

  return (
    <div>
      <header className={styles.header}>
        <div className={styles.portraitCol}>
          <span className={styles.portraitFrame}>
            <Avatar name={displayName} src={pfpSrc} size="lg" />
          </span>
          <label className={styles.changePhoto}>
            {pfpUploading ? 'Uploading…' : 'Change photo'}
            <input
              type="file"
              accept="image/*"
              className={styles.fileInput}
              onChange={uploadPfp}
              disabled={pfpUploading}
            />
          </label>
        </div>

        <div className={styles.identity}>
          <div className={styles.nameRow}>
            <h1 className={styles.name}>{displayName}</h1>
            <span className={styles.handle}>
              {profile.display_name ? `@${profile.username}` : 'CineMatch member'}
            </span>
          </div>
          <p className={styles.bio}>
            {profile.bio || 'No bio yet. A sentence about the films you keep coming back to.'}
          </p>
          <div className={styles.headerActions}>
            <Button variant="secondary" onClick={() => setEditOpen(true)}>
              Edit profile
            </Button>
            <Button variant="text" onClick={handleSignOut}>
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <dl className={styles.stats}>
        <div className={styles.stat}>
          <dt>films rated</dt>
          <dd>{formatCount(ratedCount)}</dd>
        </div>
        <div className={styles.stat}>
          <dt>average, out of five</dt>
          <dd>{average != null ? average.toFixed(2) : '—'}</dd>
        </div>
        <div className={styles.stat}>
          <dt>hearted at 4.5 or more</dt>
          <dd>{logs ? formatCount(hearted.length) : '—'}</dd>
        </div>
        <div className={styles.stat}>
          <dt>{reviewCount === 1 ? 'written note' : 'written notes'}</dt>
          <dd>{formatCount(reviewCount)}</dd>
        </div>
      </dl>

      <div className={styles.panels}>
        <section className={styles.taste} aria-labelledby="taste-title">
          <SectionHeader
            id="taste-title"
            title="Taste profile"
            description="The shape of your ratings, as the engine reads it."
          />
          {taste ? (
            <>
              <p className={styles.summary}>{taste.summary}</p>

              <div className={styles.block}>
                <h3 className={styles.blockTitle}>Genre affinity</h3>
                {loved.length ? (
                  <ul className={styles.genreList}>
                    {loved.slice(0, 5).map(([genre, value]) => (
                      <li key={genre} className={styles.genreRow}>
                        <span className={styles.genreName}>{genre}</span>
                        <span className={styles.genreTrack}>
                          <span
                            className={styles.genreFill}
                            style={{
                              width: maxGenre
                                ? `${(Number(value) / maxGenre) * 100}%`
                                : '0%',
                            }}
                          />
                        </span>
                        <span className={styles.genreValue}>
                          {Number(value).toFixed(1)}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.blockEmpty}>Not enough ratings yet.</p>
                )}
                {avoided.length ? (
                  <p className={styles.avoided}>
                    Weaker ground: {avoided.slice(0, 3).map(([genre]) => genre).join(', ')}.
                  </p>
                ) : null}
              </div>

              <div className={styles.block}>
                <h3 className={styles.blockTitle}>Review sentiment</h3>
                {sentimentTotal ? (
                  <>
                    <div
                      className={styles.sentimentBar}
                      role="img"
                      aria-label={`Review sentiment: ${distribution.positive || 0} positive, ${distribution.mixed || 0} mixed, ${distribution.negative || 0} negative`}
                    >
                      <span
                        className={styles.sentimentPositive}
                        style={{ width: segment(distribution.positive) }}
                      />
                      <span
                        className={styles.sentimentMixed}
                        style={{ width: segment(distribution.mixed) }}
                      />
                      <span
                        className={styles.sentimentNegative}
                        style={{ width: segment(distribution.negative) }}
                      />
                    </div>
                    <p className={styles.sentimentLegend}>
                      {distribution.positive || 0} positive · {distribution.mixed || 0}{' '}
                      mixed · {distribution.negative || 0} negative
                    </p>
                  </>
                ) : (
                  <p className={styles.blockEmpty}>
                    No written notes have been scored yet.
                  </p>
                )}
              </div>
            </>
          ) : (
            <p className={styles.blockEmpty}>Taste summary unavailable.</p>
          )}
        </section>

        <section className={styles.engine} aria-labelledby="engine-title">
          <SectionHeader
            id="engine-title"
            title="Recommendation engine"
            description="What is running under the shelf."
          />
          {engine ? (
            <>
              <p className={styles.engineStatus}>
                {engine.collaborative_filtering?.cf_viable
                  ? 'Collaborative filtering is active.'
                  : 'Content signals only.'}
              </p>
              <dl className={styles.engineRows}>
                {engineRows.map(([label, value]) => (
                  <div key={label} className={styles.engineRow}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
              <p className={styles.engineNote}>
                Embeddings: {engine.embedding_backend}
              </p>
            </>
          ) : (
            <p className={styles.blockEmpty}>Engine status unavailable.</p>
          )}
        </section>
      </div>

      <section className={styles.section} aria-labelledby="favorites-title">
        <SectionHeader
          id="favorites-title"
          title="Four favorites"
          description="The films you keep closest."
        />
        <div className={styles.favorites}>
          {favoriteSlots.map(({ slot, movie }) => (
            <div key={slot} className={styles.favorite}>
              {movie ? (
                <>
                  <Link
                    to={`/film/${movie.tmdb_id}`}
                    state={{ from: '/profile' }}
                    className={styles.favoritePosterLink}
                  >
                    <Poster movie={movie} className={styles.favoritePoster} />
                  </Link>
                  <div className={styles.favoriteMeta}>
                    <div className={styles.favoriteInfo}>
                      <Link
                        to={`/film/${movie.tmdb_id}`}
                        state={{ from: '/profile' }}
                        className={styles.favoriteTitle}
                      >
                        {movie.title}
                      </Link>
                      <span className={styles.favoriteSub}>
                        {[
                          movie.release_year,
                          movie.rating ? `${Number(movie.rating).toFixed(1)} / 5` : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </div>
                    <button
                      type="button"
                      className={styles.changeFavorite}
                      onClick={() => setPickerSlot(slot)}
                    >
                      Change
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className={styles.emptySlot}
                    onClick={() => setPickerSlot(slot)}
                  >
                    <span className={styles.emptyIndex}>0{slot}</span>
                    <span>Choose a film</span>
                  </button>
                  <div className={styles.favoriteMeta}>
                    <span className={styles.favoriteSub}>Open slot</span>
                    <button
                      type="button"
                      className={styles.changeFavorite}
                      onClick={() => setPickerSlot(slot)}
                    >
                      Set favorite
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      </section>

      {heartedTop.length ? (
        <section className={styles.section} aria-labelledby="hearted-title">
          <SectionHeader
            id="hearted-title"
            title="Hearted"
            description="A few of the films you keep at 4.5 and above."
            action={
              <Link to="/films" className={styles.archiveLink}>
                The archive
              </Link>
            }
          />
          <div className={styles.heartedGrid}>
            {heartedTop.map((movie) => (
              <Link
                key={movie.tmdb_id}
                to={`/film/${movie.tmdb_id}`}
                state={{ from: '/profile' }}
                className={styles.heartItem}
              >
                <Poster movie={movie} ratio="portrait" decorative />
                <span className={styles.heartTitle}>{movie.title}</span>
                <span className={styles.heartMeta}>
                  {Number(movie.rating).toFixed(1)} / 5
                </span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      <section className={styles.section} aria-labelledby="notes-title">
        <SectionHeader
          id="notes-title"
          title="Notes and numbers"
          description="What you wrote, and how the ratings fall."
          action={`${reviewList.length} ${reviewList.length === 1 ? 'note' : 'notes'}`}
        />
        <div className={styles.notesGrid}>
          <div>
            <h3 className={styles.columnTitle}>Recent notes</h3>
            {reviewList.length ? (
              <div>
                {reviewList.slice(0, 6).map((review) => (
                  <article key={review.id} className={styles.review}>
                    <div className={styles.reviewHead}>
                      <Link
                        to={`/film/${review.tmdb_id}`}
                        state={{ from: '/profile' }}
                        className={styles.reviewTitle}
                      >
                        {review.title || `Film ${review.tmdb_id}`}
                      </Link>
                      <span className={styles.reviewDate}>
                        {formatDate(review.created_at, { short: true })}
                      </span>
                    </div>
                    <blockquote className={styles.reviewQuote}>
                      “{review.content}”
                    </blockquote>
                    <p className={styles.reviewTone} data-tone={review.sentiment?.label}>
                      {review.sentiment?.label || 'mixed'} note
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <p className={styles.noNotes}>
                No notes yet. A review can be added whenever you log a film.
              </p>
            )}
          </div>

          <div>
            <h3 className={styles.columnTitle}>Rating spread</h3>
            {logs?.length ? (
              <>
                <div
                  className={styles.chart}
                  role="img"
                  aria-label={`Rating distribution. ${distributionLabel}.`}
                >
                  {ratingDistribution.map((entry) => (
                    <div key={entry.value} className={styles.bar} aria-hidden="true">
                      <span className={styles.barCount}>{entry.count || ''}</span>
                      <span className={styles.barTrack}>
                        <span
                          className={styles.barFill}
                          style={{
                            height: `${(entry.count / maxRatingCount) * 100}%`,
                          }}
                        />
                      </span>
                    </div>
                  ))}
                </div>
                <div className={styles.axis} aria-hidden="true">
                  {ratingDistribution.map((entry) => (
                    <span key={entry.value}>
                      {Number.isInteger(entry.value)
                        ? entry.value.toFixed(0)
                        : entry.value.toFixed(1)}
                    </span>
                  ))}
                </div>
                <p className={styles.spreadNote}>
                  {formatCount(hearted.length)} films sit at 4.5 or above.
                </p>
              </>
            ) : (
              <p className={styles.noNotes}>No ratings to chart yet.</p>
            )}
          </div>
        </div>
      </section>

      <section className={styles.importStrip} aria-labelledby="import-title">
        <div>
          <h2 id="import-title" className={styles.importTitle}>
            Bring your history
          </h2>
          <p className={styles.importText}>
            Have a Letterboxd export? Its ratings and watched films can seed this
            archive without retyping a thing.
          </p>
        </div>
        <Button variant="secondary" onClick={() => setImportOpen(true)}>
          Import Letterboxd
        </Button>
      </section>

      {editOpen ? (
        <EditProfileModal
          profile={profile}
          onClose={() => setEditOpen(false)}
          onSaved={saveProfile}
        />
      ) : null}

      {importOpen ? (
        <ImportDialog onClose={() => setImportOpen(false)} onImported={load} />
      ) : null}

      {pickerSlot ? (
        <FavoritePicker
          films={logs || []}
          slot={pickerSlot}
          onClose={() => setPickerSlot(null)}
          onPicked={pickFavorite}
        />
      ) : null}
    </div>
  )
}
