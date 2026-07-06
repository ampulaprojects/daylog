import sqlite3
import sys

DB_PATH = "daylog.db"


def counts(conn):
    u = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    e = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    ev = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return u, e, ev


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    u_before, e_before, ev_before = counts(conn)
    print(f"PRED migráciou: users={u_before}, entries={e_before}, events={ev_before}")

    # ── users_new ──────────────────────────────────────────────────────────────
    conn.execute("DROP TABLE IF EXISTS users_new")
    conn.execute("""
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO users_new (id, username, hashed_password, role, created_at)
        SELECT id, username, hashed_password, role, created_at
        FROM users
    """)
    u_new = conn.execute("SELECT COUNT(*) FROM users_new").fetchone()[0]
    print(f"users_new: {u_new} riadkov")

    # ── entries_new ────────────────────────────────────────────────────────────
    conn.execute("DROP TABLE IF EXISTS entries_new")
    conn.execute("""
        CREATE TABLE entries_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_time TEXT,
            text TEXT NOT NULL,
            source TEXT DEFAULT 'typed',
            llm_analysis TEXT,
            llm_processed_at TEXT,
            llm_model TEXT
        )
    """)
    conn.execute("""
        INSERT INTO entries_new
            (id, user_id, created_at, entry_date, entry_time, text, source,
             llm_analysis, llm_processed_at, llm_model)
        SELECT
            id,
            COALESCE(user_id, 1),
            created_at,
            entry_date,
            entry_time,
            text,
            COALESCE(source, 'typed'),
            llm_analysis,
            llm_processed_at,
            llm_model
        FROM entries
    """)
    e_new = conn.execute("SELECT COUNT(*) FROM entries_new").fetchone()[0]
    print(f"entries_new: {e_new} riadkov")

    # ── swap ──────────────────────────────────────────────────────────────────
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")

    conn.execute("DROP TABLE entries")
    conn.execute("ALTER TABLE entries_new RENAME TO entries")

    conn.commit()

    u_after, e_after, ev_after = counts(conn)
    print(f"\nPO migrácii: users={u_after}, entries={e_after}, events={ev_after}")

    ok = (u_after == u_before and e_after == e_before and ev_after == ev_before)
    print(f"Kontrola počtov: {'OK' if ok else 'CHYBA — počty nesedia!'}")

    # ── PRAGMA výpis ──────────────────────────────────────────────────────────
    for tbl in ("users", "entries", "events"):
        print(f"\nPRAGMA table_info({tbl}):")
        for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall():
            pk = " PK" if row[5] else ""
            nn = " NOT NULL" if row[3] else ""
            df = f" DEFAULT {row[4]}" if row[4] is not None else ""
            print(f"  {row[0]:2d}  {row[1]:<20s} {row[2]}{pk}{nn}{df}")

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    run()
