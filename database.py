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
            created_at TEXT NOT NULL
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
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN user_id INTEGER REFERENCES users(id)")
    except Exception:
        pass
    conn.commit()
    conn.close()


def create_user(username: str, password: str) -> bool:
    from auth import hash_password
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO users (username, hashed_password, created_at) VALUES (?,?,?)",
            (username, hash_password(password), now)
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


def get_user_by_id(user_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


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
           llm_analysis = ?,
           llm_tags = ?,
           llm_mood = ?,
           llm_processed_at = ?,
           llm_model = ?
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
