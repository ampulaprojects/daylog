from datetime import datetime
import sqlite3
import json
import os

DB_PATH = os.environ.get("DAYLOG_DB", "daylog.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whitelist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            added_by TEXT,
            added_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            title TEXT,
            text TEXT NOT NULL,
            mood TEXT,
            tags TEXT DEFAULT '[]',
            source TEXT DEFAULT 'typed',
            llm_analysis TEXT,
            llm_tags TEXT DEFAULT '[]',
            llm_mood TEXT,
            llm_processed_at TEXT,
            llm_model TEXT,
            user_id INTEGER REFERENCES users(id)
        )
    """)
    for col, definition in [
        ("user_id", "INTEGER REFERENCES users(id)"),
        ("email", "TEXT"),
        ("role", "TEXT NOT NULL DEFAULT 'user'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        except Exception:
            pass
        try:
            conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {definition}")
        except Exception:
            pass
    conn.commit()
    conn.close()


# ── Users ──────────────────────────────────────────────────────────────────

def create_user(username: str, password: str, email: str = None, role: str = "user") -> bool:
    from auth import hash_password
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO users (username, hashed_password, email, role, created_at) VALUES (?,?,?,?,?)",
            (username, hash_password(password), email, role, now)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_user_by_username(username: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_email(email: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, email, role, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_user_role(username: str, role: str):
    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    conn.commit()
    conn.close()


def change_user_password(username: str, new_password: str):
    from auth import hash_password
    conn = get_db()
    conn.execute(
        "UPDATE users SET hashed_password = ? WHERE username = ?",
        (hash_password(new_password), username)
    )
    conn.commit()
    conn.close()


# ── Whitelist ──────────────────────────────────────────────────────────────

def add_to_whitelist(email: str, added_by: str = "system"):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO whitelist (email, added_by, added_at) VALUES (?,?,?)",
            (email.lower().strip(), added_by, now)
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def remove_from_whitelist(email: str):
    conn = get_db()
    conn.execute("DELETE FROM whitelist WHERE email = ?", (email.lower().strip(),))
    conn.commit()
    conn.close()


def is_in_whitelist(email: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM whitelist WHERE email = ?", (email.lower().strip(),)
    ).fetchone()
    conn.close()
    return row is not None


def get_whitelist():
    conn = get_db()
    rows = conn.execute("SELECT * FROM whitelist ORDER BY added_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Entries ────────────────────────────────────────────────────────────────

def create_entry(entry_date, text, title=None, mood=None, tags=None, source="typed", user_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(tags or [])
    cur = conn.execute(
        """INSERT INTO entries
           (created_at, entry_date, title, text, mood, tags, source, user_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now, entry_date, title, text, mood, tags_json, source, user_id)
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return entry_id


def update_llm_analysis(entry_id, analysis, llm_tags, llm_mood, model_name):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        """UPDATE entries SET
           llm_analysis = ?, llm_tags = ?, llm_mood = ?,
           llm_processed_at = ?, llm_model = ?
           WHERE id = ?""",
        (json.dumps(analysis), json.dumps(llm_tags), llm_mood, now, model_name, entry_id)
    )
    conn.commit()
    conn.close()


def get_entries(search=None, mood=None, limit=50, user_id=None):
    conn = get_db()
    query = "SELECT * FROM entries WHERE 1=1"
    params = []
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    if search:
        query += " AND text LIKE ?"
        params.append(f"%{search}%")
    if mood:
        query += " AND mood = ?"
        params.append(mood)
    query += " ORDER BY entry_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entry(entry_id, user_id=None):
    conn = get_db()
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None
