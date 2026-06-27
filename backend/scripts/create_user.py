from models.database import init_db, SessionLocal, User, UserProfile, engine, DATABASE_URL
from routers.profile import normalize_username
import sqlite3
import os


def create_user(username: str):
    init_db()
    db = SessionLocal()
    try:
        # Ensure `password_hash` column exists on `users` table for current schema
        try:
            if DATABASE_URL.startswith('sqlite:///'):
                dbfile = DATABASE_URL.replace('sqlite:///', '')
                if os.path.exists(dbfile):
                    conn = sqlite3.connect(dbfile)
                    cur = conn.cursor()
                    # users.password_hash
                    cur.execute("PRAGMA table_info('users')")
                    cols = [r[1] for r in cur.fetchall()]
                    if 'password_hash' not in cols:
                        cur.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
                        conn.commit()
                        print('Added missing column users.password_hash')
                    # user_profile.display_name
                    cur.execute("PRAGMA table_info('user_profile')")
                    cols2 = [r[1] for r in cur.fetchall()]
                    if 'display_name' not in cols2:
                        cur.execute("ALTER TABLE user_profile ADD COLUMN display_name TEXT")
                        conn.commit()
                        print('Added missing column user_profile.display_name')
                    conn.close()
        except Exception as e:
            print('Could not ensure schema columns via sqlite:', e)

        uname = normalize_username(username)
        existing = db.query(User).filter(User.username == uname).first()
        if existing:
            print(f"User '{uname}' already exists (user_id={existing.user_id})")
            return
        user = User(username=uname)
        db.add(user)
        profile = UserProfile(username=uname)
        db.add(profile)
        db.commit()
        print(f"Created user and profile for '{uname}'")
    finally:
        db.close()


if __name__ == '__main__':
    create_user('@mustafa')
