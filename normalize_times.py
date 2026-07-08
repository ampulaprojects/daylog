"""
Jednorazový migračný skript: normalizuje event_time H:MM → HH:MM v DB.
Spusti lokálne: python normalize_times.py
Spusti na VPS:  python normalize_times.py --apply
"""
import sys
import sqlite3
import os

DB_PATH = os.environ.get("DAYLOG_DB", "daylog.db")
DRY_RUN = "--apply" not in sys.argv


def normalize_time(s):
    if not s:
        return s
    parts = s.strip().split(':')
    if len(parts) == 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    return s


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT id, event_time FROM events WHERE event_time IS NOT NULL AND event_time != ''"
).fetchall()

changes = []
for row in rows:
    normalized = normalize_time(row["event_time"])
    if normalized != row["event_time"]:
        changes.append((row["id"], row["event_time"], normalized))

print(f"DB: {DB_PATH}")
print(f"Celkom eventov s časom: {len(rows)}")
print(f"Záznamov na normalizáciu: {len(changes)}")

if changes:
    print()
    print(f"{'ID':>6}  {'pred':>8}  →  {'po':>8}")
    print("-" * 32)
    for eid, before, after in changes:
        print(f"{eid:>6}  {before:>8}  →  {after:>8}")

if DRY_RUN:
    print()
    print("Dry-run — žiadne zmeny. Spusti s --apply pre zápis.")
else:
    for eid, _, normalized in changes:
        conn.execute("UPDATE events SET event_time = ? WHERE id = ?", (normalized, eid))
    conn.commit()
    print()
    print(f"Aplikované: {len(changes)} zmien.")

conn.close()
