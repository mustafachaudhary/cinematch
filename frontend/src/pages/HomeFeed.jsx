import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../hooks/api'
import useTitle from '../hooks/useTitle'
import { formatRuntime, formatScore, plainReason } from '../lib/format'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import PageHeader from '../components/PageHeader'
import Poster from '../components/Poster'
import PosterRun from '../components/PosterRun'
import SectionHeader from '../components/SectionHeader'
import Skeleton from '../components/Skeleton'
import { useToast } from '../components/Toast'
import styles from './HomeFeed.module.css'

function HomeSkeleton() {
  return (
    <div>
      <h1 className={styles.skeletonTitle}>Tonight&rsquo;s print</h1>
      <div className={styles.skeletonHero}>
        <Skeleton className={styles.skeletonSmall} ratio="2 / 3" />
        <div className={styles.skeletonCopy}>
          <Skeleton className={styles.skeletonHeading} />
          <Skeleton className={styles.skeletonLine} />
          <Skeleton className={styles.skeletonLine} />
        </div>
        <Skeleton className={styles.skeletonTall} ratio="3 / 4" />
        <Skeleton className={styles.skeletonWide} ratio="16 / 10" />
      </div>
    </div>
  )
}

function feedSessionId() {
  const key = 'cinematch_feed_session'
  let value = sessionStorage.getItem(key)
  if (!value) {
    value =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`
    sessionStorage.setItem(key, value)
  }
  return value
}

export default function HomeFeed() {
  const toast = useToast()
  const [feed, setFeed] = useState(null)
  const [feature, setFeature] = useState(null)
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  useTitle('Discover')

  const load = async ({ quiet = false, exclude = [] } = {}) => {
    if (quiet) setRefreshing(true)
    else setError(null)
    try {
      const data = await api.getFeed(16, exclude)
      setFeed(data)
      setError(null)
    } catch (err) {
      if (quiet && feed) toast(err.message, 'error')
      else setError(err.message)
    } finally {
      setRefreshing(false)
    }
  }

  const refreshPicks = () => {
    const seen = (feed?.results || []).map((movie) => movie.tmdb_id)
    load({ quiet: true, exclude: seen })
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const ids = (feed?.results || []).map((movie) => movie.tmdb_id).filter(Boolean)
    if (!ids.length) return
    api.recordImpressions(ids, feedSessionId()).catch(() => {
      /* impressions are best-effort and must never break the feed */
    })
  }, [feed])

  const featured = feed?.results?.[0]
  const second = feed?.results?.[1]
  const third = feed?.results?.[2]
  const run = feed?.results?.slice(3, 16) || []

  useEffect(() => {
    if (!featured) {
      setFeature(null)
      return undefined
    }
    let active = true
    api
      .getMovie(featured.tmdb_id, { match: false })
      .then((detail) => {
        if (active) setFeature(detail)
      })
      .catch(() => {
        if (active) setFeature(null)
      })
    return () => {
      active = false
    }
  }, [featured?.tmdb_id])

  if (error) {
    return (
      <div>
        <PageHeader title="Tonight’s print" />
        <EmptyState
          title="The projector is dark."
          action={
            <Button onClick={() => load()} variant="secondary">
              Try again
            </Button>
          }
        >
          {error}. Check that the CineMatch API is running, then try the feed again.
        </EmptyState>
      </div>
    )
  }

  if (!feed) return <HomeSkeleton />

  if (!featured) {
    return (
      <div>
        <PageHeader title="Tonight’s print" />
        <EmptyState
          title="Nothing to project yet."
          action={
            <Button onClick={refreshPicks} variant="secondary">
              Refresh picks
            </Button>
          }
        >
          The catalog came back empty. Once films are available, a recommendation will
          be waiting here.
        </EmptyState>
      </div>
    )
  }

  const personalized = feed.feed_type === 'personalized'
  const match = formatScore(featured.score)
  const runtime = formatRuntime(feature?.runtime || featured.runtime)

  return (
    <div>
      <section className={styles.hero} aria-labelledby="home-title">
        <div className={styles.smallStack}>
          {[second, third].filter(Boolean).map((film) => (
            <Link
              key={film.tmdb_id}
              to={`/film/${film.tmdb_id}`}
              state={{ from: '/' }}
              className={styles.smallFigure}
            >
              <Poster movie={film} ratio="poster" decorative loading="eager" />
              <span className={styles.smallCaption}>
                <span className={styles.smallTitle}>{film.title}</span>
                <span className={styles.smallYear}>{film.release_year}</span>
              </span>
            </Link>
          ))}
        </div>

        <div className={styles.copy}>
          <h1 id="home-title" className={styles.title}>
            {personalized ? 'Tonight’s print' : 'A place to start'}
          </h1>
          <p className={styles.support}>
            {personalized
              ? feed.profile_summary
              : 'Critically strong, widely loved films while the engine learns the shape of your taste.'}
          </p>
          <Link
            to={`/film/${featured.tmdb_id}`}
            state={{ from: '/' }}
            className={styles.primaryLink}
          >
            Open the film
          </Link>
        </div>

        <Link
          to={`/film/${featured.tmdb_id}`}
          state={{ from: '/' }}
          className={styles.tallFigure}
        >
          <Poster movie={featured} ratio="portrait" decorative loading="eager" />
          <span className={styles.tallCaption}>
            <span className={styles.filmTitle}>{featured.title}</span>
            <span className={styles.filmMeta}>
              {match ? <span className={styles.match}>{match} match</span> : null}
              {match && (runtime || featured.release_year) ? ' \u00b7 ' : null}
              {[runtime, featured.release_year].filter(Boolean).join(' \u00b7 ')}
            </span>
            {featured.reason ? (
              <span className={styles.reason}>{plainReason(featured.reason)}</span>
            ) : null}
          </span>
        </Link>

        <div className={styles.wideFigure}>
          <Link to={`/film/${featured.tmdb_id}`} state={{ from: '/' }}>
            <Poster
              movie={featured}
              src={feature?.backdrop_path}
              ratio="wide"
              decorative
              loading="eager"
            />
          </Link>
          <p className={styles.wideCaption}>
            {feature?.backdrop_path
              ? `A still from ${featured.title}.`
              : `A print from ${featured.title}.`}
          </p>
        </div>
      </section>

      {run.length ? (
        <section className={styles.runSection} aria-labelledby="also-tonight">
          <SectionHeader
            title="Also tonight"
            description="More prints from the top of the ranking."
            action={
              <Button
                variant="text"
                onClick={refreshPicks}
                disabled={refreshing}
              >
                {refreshing ? 'Refreshing…' : 'Refresh picks'}
              </Button>
            }
          />
          <PosterRun
            films={run}
            liftedIndex={2}
            ariaLabel="More recommended films"
            fact={(film) => {
              const score = formatScore(film.score)
              return score ? `${score} match` : film.release_year || ''
            }}
          />
        </section>
      ) : null}
    </div>
  )
}
