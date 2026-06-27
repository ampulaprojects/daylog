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
            llm_model TEXT
        )
    """)
    conn.commit()
    conn.close()

def create_entry(entry_date, text, title=None, mood=None, tags=None, source="typed"):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(tags or [])
    cur = conn.execute(
        """INSERT INTO entries
           (created_at, entry_date, title, text, mood, tags, source)
           VALUES (?,?,?,?,?,?,?)""",
        (now, entry_date, title, text, mood, tags_json, source)
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

def get_entries(search=None, mood=None, limit=50):
    conn = get_db()
    query = "SELECT * FROM entries WHERE 1=1"
    params = []
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

def get_entry(entry_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None
