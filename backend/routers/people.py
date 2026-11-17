"""Person filmography pages.

The catalog enrichment pass stores top cast plus directors/writers in
``film_people``. This router turns those credit rows into a browsable person
catalog, so a cast name on a film overview can open every catalog film that
person worked on.
"""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models.database import FilmPerson, LoggedMovie, MovieCache, get_db
from routers.deps import format_movie, resolve_user

router = APIRouter(prefix="/people", tags=["people"])

ROLE_ORDER = {"director": 0, "writer": 1, "cast": 2}
ROLE_LABELS = {
    "director": "Directing",
    "writer": "Writing",
    "cast": "Acting",
}


@router.get("/{person_id}")
def get_person(
    person_id: int,
    db: Session = Depends(get_db),
    user=Depends(resolve_user),
):
    rows = (
        db.query(FilmPerson, MovieCache)
        .join(MovieCache, MovieCache.tmdb_id == FilmPerson.tmdb_id)
        .filter(FilmPerson.person_id == person_id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Person not found in the catalog")

    name = None
    profile_path = None
    credits = {}
    role_counts = defaultdict(int)
    for credit, movie in rows:
        name = name or credit.name
        profile_path = profile_path or (credit.profile_path or None)
        entry = credits.get(movie.tmdb_id)
        if entry is None:
            entry = {
                "movie": movie,
                "roles": set(),
                "ordering": credit.ordering,
            }
            credits[movie.tmdb_id] = entry
        entry["roles"].add(credit.role)
        if credit.ordering is not None:
            entry["ordering"] = min(
                entry["ordering"] if entry["ordering"] is not None else credit.ordering,
                credit.ordering,
            )
        role_counts[credit.role] += 1

    film_ids = list(credits.keys())
    logged = {
        tmdb_id: (rating, logged_at)
        for tmdb_id, rating, logged_at in db.query(
            LoggedMovie.tmdb_id, LoggedMovie.rating, LoggedMovie.logged_at
        )
        .filter(
            LoggedMovie.user_id == user.user_id,
            LoggedMovie.tmdb_id.in_(film_ids),
        )
        .all()
    }

    films = []
    for entry in credits.values():
        roles = sorted(entry["roles"], key=lambda role: ROLE_ORDER.get(role, 9))
        payload = format_movie(entry["movie"])
        user_rating, logged_at = logged.get(entry["movie"].tmdb_id, (None, None))
        payload.update(
            {
                "roles": roles,
                "role_labels": [ROLE_LABELS.get(role, role.title()) for role in roles],
                "cast_order": entry["ordering"],
                "runtime": entry["movie"].runtime,
                "vote_count": entry["movie"].vote_count,
                "user_rating": user_rating,
                "logged_at": logged_at.isoformat() if logged_at else None,
            }
        )
        films.append(payload)

    # Default view is newest first, then the more prominent catalog title.
    # The client can re-sort the full filmography without another request.
    films.sort(
        key=lambda film: (
            -(film.get("release_year") or 0),
            -(film.get("popularity") or 0),
            film.get("title") or "",
        )
    )

    role_entries = [
        {
            "role": role,
            "label": ROLE_LABELS.get(role, role.title()),
            "count": count,
        }
        for role, count in sorted(
            role_counts.items(), key=lambda item: ROLE_ORDER.get(item[0], 9)
        )
    ]

    return {
        "person_id": person_id,
        "name": name,
        "profile_path": profile_path,
        "roles": role_entries,
        "film_count": len(films),
        "films": films,
    }
