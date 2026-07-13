"""
Migrácia med_schedule na v2 schému:
  - count: TEXT → REAL
  - pridanie stĺpca time_exact TEXT

Tabuľka sa vyprázdni (testovacie dáta sa neprenášajú).
Spusti: python migrate_meds_v2.py
"""
import sqlite3
import os

DB_PATH = os.environ.get("DAYLOG_DB", "daylog.db")


def migrate():
    conn = sqlite3.connect(DB_PATH)
    print(f"DB: {DB_PATH}")

    conn.execute("DROP TABLE IF EXISTS med_schedule")
    conn.execute("""
        CREATE TABLE med_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'liek',
            count REAL,
            dose TEXT,
            unit TEXT,
            time_type TEXT,
            time_exact TEXT,
            time_value TEXT,
            days TEXT DEFAULT 'kazdy_den',
            note TEXT,
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

    rows = conn.execute("SELECT COUNT(*) FROM med_schedule").fetchone()[0]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(med_schedule)").fetchall()]
    types = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(med_schedule)").fetchall()}

    print(f"Stĺpce: {cols}")
    print(f"count typ: {types.get('count')}")
    print(f"time_exact prítomný: {'time_exact' in cols}")
    print(f"Počet záznamov: {rows}")
    print("Migrácia úspešná.")
    conn.close()


if __name__ == "__main__":
    migrate()
