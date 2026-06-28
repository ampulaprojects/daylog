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
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT DEFAULT 'typed',
            llm_analysis TEXT,
            llm_processed_at TEXT,
            llm_model TEXT,
            user_id INTEGER REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER REFERENCES entries(id),
            user_id INTEGER REFERENCES users(id),
            event_time TEXT,
            event_type TEXT,
            value TEXT,
            note TEXT,
            confirmed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    # migrations for existing DBs
    for col, definition in [
        ("user_id",    "INTEGER REFERENCES users(id)"),
        ("role",       "TEXT NOT NULL DEFAULT 'user'"),
        ("entry_time", "TEXT"),
    ]:
        for table in ("users", "entries"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            except Exception:
                pass
    conn.commit()
    conn.close()


# ── Users ──────────────────────────────────────────────────────────────────

def create_user(username: str, password: str, role: str = "user") -> bool:
    from auth import hash_password
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO users (username, hashed_password, role, created_at) VALUES (?,?,?,?)",
            (username, hash_password(password), role, now)
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


def get_all_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_password(user_id: int, new_password: str):
    from auth import hash_password
    conn = get_db()
    conn.execute(
        "UPDATE users SET hashed_password = ? WHERE id = ?",
        (hash_password(new_password), user_id)
    )
    conn.commit()
    conn.close()


def set_user_role(username: str, role: str):
    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    conn.commit()
    conn.close()


# ── Entries ────────────────────────────────────────────────────────────────

def create_entry(entry_date, text, entry_time=None, source="typed", user_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO entries
           (created_at, entry_date, entry_time, text, source, user_id)
           VALUES (?,?,?,?,?,?)""",
        (now, entry_date, entry_time, text, source, user_id)
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


def get_entries(search=None, limit=50):
    conn = get_db()
    query = """
        SELECT e.id, e.created_at, e.entry_date, e.entry_time, e.text, e.source,
               e.llm_analysis, e.llm_processed_at, e.llm_model, e.user_id,
               u.username AS author
        FROM entries e
        LEFT JOIN users u ON e.user_id = u.id
        WHERE 1=1
    """
    params = []
    if search:
        query += " AND e.text LIKE ?"
        params.append(f"%{search}%")
    query += " ORDER BY e.entry_date DESC, e.entry_time DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entry(entry_id):
    conn = get_db()
    row = conn.execute(
        """SELECT e.id, e.created_at, e.entry_date, e.entry_time, e.text, e.source,
                  e.llm_analysis, e.llm_processed_at, e.llm_model, e.user_id,
                  u.username AS author
           FROM entries e
           LEFT JOIN users u ON e.user_id = u.id
           WHERE e.id = ?""",
        (entry_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Events ─────────────────────────────────────────────────────────────────

def create_event(entry_id, user_id, event_type, value, event_time=None, note=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO events
           (entry_id, user_id, event_time, event_type, value, note, confirmed, created_at)
           VALUES (?,?,?,?,?,?,0,?)""",
        (entry_id, user_id, event_time, event_type, value, note, now)
    )
    conn.commit()
    event_id = cur.lastrowid
    conn.close()
    return event_id


def get_events_by_entry(entry_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM events WHERE entry_id = ? ORDER BY event_time, created_at",
        (entry_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_events_by_date(date):
    conn = get_db()
    rows = conn.execute(
        """SELECT ev.* FROM events ev
           JOIN entries e ON ev.entry_id = e.id
           WHERE e.entry_date = ?
           ORDER BY ev.event_time, ev.created_at""",
        (date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def confirm_event(event_id):
    conn = get_db()
    conn.execute("UPDATE events SET confirmed = 1 WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


def update_event(event_id, **kwargs):
    if not kwargs:
        return
    allowed = {"event_time", "event_type", "value", "note", "confirmed"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db()
    conn.execute(
        f"UPDATE events SET {set_clause} WHERE id = ?",
        (*fields.values(), event_id)
    )
    conn.commit()
    conn.close()


def delete_event(event_id):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
