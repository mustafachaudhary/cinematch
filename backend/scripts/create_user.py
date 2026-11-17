"""Create a local user + profile row (utility script)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.database import SessionLocal, User, UserProfile, init_db  # noqa: E402
from routers.deps import normalize_username  # noqa: E402


def create_user(username: str):
    init_db()
    db = SessionLocal()
    try:
        uname = normalize_username(username)
        existing = db.query(User).filter(User.username == uname).first()
        if existing:
            print(f"User '{uname}' already exists (user_id={existing.user_id})")
            return
        user = User(username=uname)
        db.add(user)
        db.flush()
        profile = UserProfile(username=uname)
        db.add(profile)
        db.commit()
        print(f"Created user '{uname}' (user_id={user.user_id}) with profile")
    finally:
        db.close()


if __name__ == "__main__":
    create_user(sys.argv[1] if len(sys.argv) > 1 else "@mustafa")
