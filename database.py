from datetime import datetime
import sqlite3
import os
import json

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
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_time TEXT,
            text TEXT NOT NULL,
            source TEXT DEFAULT 'typed',
            llm_analysis TEXT,
            llm_processed_at TEXT,
            llm_model TEXT,
            photo_path TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN photo_path TEXT")
    except Exception:
        pass
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
            created_at TEXT NOT NULL,
            catalog_id INTEGER REFERENCES med_catalog(id)
        )
    """)
    try:
        conn.execute("ALTER TABLE events ADD COLUMN catalog_id INTEGER")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS med_schedule (
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
            catalog_id INTEGER REFERENCES med_catalog(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    try:
        conn.execute("ALTER TABLE med_schedule ADD COLUMN catalog_id INTEGER")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS med_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL,
            aliases TEXT DEFAULT '[]',
            kind TEXT DEFAULT 'liek',
            strength TEXT,
            form TEXT,
            manufacturer TEXT,
            sukl_code TEXT,
            atc_code TEXT,
            description TEXT,
            side_effects TEXT,
            personal_notes TEXT,
            info_source TEXT,
            photo_path TEXT,
            photos TEXT DEFAULT '[]',
            extracted_raw TEXT,
            pil_info TEXT,
            pil_source TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    try:
        conn.execute("ALTER TABLE med_catalog ADD COLUMN photos TEXT DEFAULT '[]'")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE med_catalog ADD COLUMN extracted_raw TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE med_catalog ADD COLUMN pil_info TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE med_catalog ADD COLUMN pil_source TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE med_catalog ADD COLUMN pil_last_attempt TEXT")
    except Exception:
        pass
    # migrácia: existujúci photo_path skopíruj do photos ako prvý prvok (raz)
    conn.execute(
        """UPDATE med_catalog
           SET photos = '["' || replace(photo_path, '"', '') || '"]'
           WHERE photo_path IS NOT NULL AND photo_path != ''
             AND (photos IS NULL OR photos = '[]' OR photos = '')"""
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            function TEXT NOT NULL,
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            web_searches INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            user_id INTEGER,
            context TEXT
        )
    """)
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

def create_entry(entry_date, text, entry_time=None, source="typed", user_id=None, photo_path=None,
                 llm_analysis=None, llm_model=None, llm_processed_at=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO entries
           (user_id, created_at, entry_date, entry_time, text, source, photo_path,
            llm_analysis, llm_model, llm_processed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user_id, now, entry_date, entry_time, text, source, photo_path,
         llm_analysis, llm_model, llm_processed_at)
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return entry_id



def get_entries(search=None, limit=50, with_events=False):
    conn = get_db()
    query = """
        SELECT e.id, e.created_at, e.entry_date, e.entry_time, e.text, e.source,
               e.llm_analysis, e.llm_processed_at, e.llm_model, e.user_id, e.photo_path,
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
    entries = [dict(r) for r in rows]

    if with_events and entries:
        ids = [e["id"] for e in entries]
        placeholders = ",".join("?" * len(ids))
        ev_rows = conn.execute(
            f"""SELECT ev.*, mc.canonical_name AS catalog_name
                FROM events ev
                LEFT JOIN med_catalog mc ON ev.catalog_id = mc.id
                WHERE ev.entry_id IN ({placeholders})
                ORDER BY ev.event_time, ev.created_at""",
            ids
        ).fetchall()
        evs_by_id: dict = {}
        for ev in ev_rows:
            d = dict(ev)
            evs_by_id.setdefault(d["entry_id"], []).append(d)
        for e in entries:
            e["events"] = evs_by_id.get(e["id"], [])

    conn.close()
    return entries


def get_entry(entry_id):
    conn = get_db()
    row = conn.execute(
        """SELECT e.id, e.created_at, e.entry_date, e.entry_time, e.text, e.source,
                  e.llm_analysis, e.llm_processed_at, e.llm_model, e.user_id, e.photo_path,
                  u.username AS author
           FROM entries e
           LEFT JOIN users u ON e.user_id = u.id
           WHERE e.id = ?""",
        (entry_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Events ─────────────────────────────────────────────────────────────────

def normalize_time(s):
    """'6:00' → '06:00', '14:25' → '14:25', unparseable/empty → as-is."""
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


def create_event(entry_id, user_id, event_type, value, event_time=None, note=None, catalog_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO events
           (entry_id, user_id, event_time, event_type, value, note, catalog_id, confirmed, created_at)
           VALUES (?,?,?,?,?,?,?,0,?)""",
        (entry_id, user_id, normalize_time(event_time), event_type, value, note, catalog_id, now)
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


def delete_entry(entry_id: int):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE entry_id = ?", (entry_id,))
    conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()


def update_entry_text(entry_id: int, text: str, entry_date: str = None, entry_time: str = None):
    conn = get_db()
    if entry_date is not None:
        conn.execute(
            "UPDATE entries SET text=?, entry_date=?, entry_time=? WHERE id=?",
            (text, entry_date, entry_time or None, entry_id)
        )
    else:
        conn.execute("UPDATE entries SET text=? WHERE id=?", (text, entry_id))
    conn.commit()
    conn.close()


def replace_entry_events(entry_id: int, user_id: int, events: list):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE entry_id = ?", (entry_id,))
    now = datetime.utcnow().isoformat()
    for ev in events:
        conn.execute(
            """INSERT INTO events
               (entry_id, user_id, event_time, event_type, value, note, catalog_id, confirmed, created_at)
               VALUES (?,?,?,?,?,?,?,1,?)""",
            (entry_id, user_id, normalize_time(ev.get("event_time")), ev.get("event_type"),
             ev.get("value"), ev.get("note"), ev.get("catalog_id"), now)
        )
    conn.commit()
    conn.close()


def delete_event(event_id):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


# ── Medications ────────────────────────────────────────────────────────────

def get_medications(include_inactive=False):
    conn = get_db()
    q = """SELECT m.*, c.canonical_name AS catalog_name
           FROM med_schedule m
           LEFT JOIN med_catalog c ON m.catalog_id = c.id"""
    if not include_inactive:
        q += " WHERE m.active = 1"
    q += " ORDER BY m.sort_order, m.time_value, m.name"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_medication(name, kind="liek", count=None, dose=None, unit=None,
                      time_type=None, time_exact=None, time_value=None,
                      days="kazdy_den", note=None, sort_order=0, catalog_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO med_schedule
           (name, kind, count, dose, unit, time_type, time_exact, time_value, days, note,
            active, sort_order, catalog_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
        (name, kind, count, dose, unit, time_type, time_exact, time_value, days, note,
         sort_order, catalog_id, now, now)
    )
    conn.commit()
    med_id = cur.lastrowid
    conn.close()
    return med_id


def update_medication(med_id, name, kind, count, dose, unit, time_type,
                      time_exact, time_value, days, note, sort_order, catalog_id=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        """UPDATE med_schedule SET name=?, kind=?, count=?, dose=?, unit=?,
           time_type=?, time_exact=?, time_value=?, days=?, note=?,
           sort_order=?, catalog_id=?, updated_at=? WHERE id=?""",
        (name, kind, count, dose, unit, time_type, time_exact, time_value,
         days, note, sort_order, catalog_id, now, med_id)
    )
    conn.commit()
    conn.close()


def delete_medication(med_id):
    conn = get_db()
    conn.execute("DELETE FROM med_schedule WHERE id=?", (med_id,))
    conn.commit()
    conn.close()


def set_medication_active(med_id, active):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE med_schedule SET active=?, updated_at=? WHERE id=?",
                 (int(active), now, med_id))
    conn.commit()
    conn.close()


def reorder_medications(items):
    """items: list of (id, sort_order) tuples"""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    for med_id, sort_order in items:
        conn.execute("UPDATE med_schedule SET sort_order=?, updated_at=? WHERE id=?",
                     (sort_order, now, med_id))
    conn.commit()
    conn.close()


# ── Med catalog (referenčná príručka) ────────────────────────────────────────

_CATALOG_COLS = (
    "canonical_name", "aliases", "kind", "strength", "form", "manufacturer",
    "sukl_code", "atc_code", "description", "side_effects", "personal_notes",
    "info_source", "photo_path"
)


def get_catalog(include_inactive=False):
    conn = get_db()
    q = "SELECT * FROM med_catalog"
    if not include_inactive:
        q += " WHERE active = 1"
    q += " ORDER BY canonical_name COLLATE NOCASE"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_catalog_item(item_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM med_catalog WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_catalog_item(canonical_name, aliases="[]", kind="liek", strength=None,
                        form=None, manufacturer=None, sukl_code=None, atc_code=None,
                        description=None, side_effects=None, personal_notes=None,
                        info_source=None, photo_path=None, photos="[]", extracted_raw=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO med_catalog
           (canonical_name, aliases, kind, strength, form, manufacturer, sukl_code,
            atc_code, description, side_effects, personal_notes, info_source,
            photo_path, photos, extracted_raw, active, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
        (canonical_name, aliases, kind, strength, form, manufacturer, sukl_code,
         atc_code, description, side_effects, personal_notes, info_source,
         photo_path, photos, extracted_raw, now, now)
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return item_id


def update_catalog_item(item_id, canonical_name, aliases, kind, strength, form,
                        manufacturer, sukl_code, atc_code, description, side_effects,
                        personal_notes, info_source, photo_path, photos="[]",
                        extracted_raw=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        """UPDATE med_catalog SET canonical_name=?, aliases=?, kind=?, strength=?,
           form=?, manufacturer=?, sukl_code=?, atc_code=?, description=?,
           side_effects=?, personal_notes=?, info_source=?, photo_path=?, photos=?,
           extracted_raw=?, updated_at=? WHERE id=?""",
        (canonical_name, aliases, kind, strength, form, manufacturer, sukl_code,
         atc_code, description, side_effects, personal_notes, info_source,
         photo_path, photos, extracted_raw, now, item_id)
    )
    conn.commit()
    conn.close()


def update_catalog_pil(item_id, pil_info, pil_source):
    """Uloží úspešne dohľadané PIL dáta. Zmaže príznak neúspešného pokusu."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE med_catalog SET pil_info=?, pil_source=?, pil_last_attempt=NULL, updated_at=? WHERE id=?",
        (pil_info, pil_source, now, item_id))
    conn.commit()
    conn.close()


def mark_pil_not_found(item_id, when):
    """Zaznamená neúspešný PIL pokus (dátum) — aby sa drahé hľadanie neopakovalo omylom."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE med_catalog SET pil_last_attempt=?, updated_at=? WHERE id=?",
                 (when, now, item_id))
    conn.commit()
    conn.close()


# ── Zlúčenie duplicitných liekov (citlivá operácia — transakcia) ─────────────

_MERGE_FIELDS = ("canonical_name", "kind", "strength", "form", "manufacturer",
                 "sukl_code", "atc_code", "description", "side_effects",
                 "personal_notes", "info_source", "extracted_raw", "pil_info", "pil_source")


def _has(v):
    return v not in (None, "", "[]", "{}")


def _dedup_ci(values):
    """Dedup reťazcov case-insensitive, zachovaj poradie a prvý zápis."""
    seen, out = set(), []
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def merge_catalog_items(keep_id, merge_id, field_choices=None, main_photo=None):
    """Zlúči liek B (merge_id) do A (keep_id). Poradie: uprav A → prepoj eventy →
    over že nie sú osirené → zmaž B. Celé v jednej transakcii (rollback pri chybe).
    field_choices: {pole: 'keep'|'merge'} pre polia kde sa líšia. Vráti súhrn."""
    if keep_id == merge_id:
        raise ValueError("Nemôžeš zlúčiť liek sám so sebou")
    field_choices = field_choices or {}

    conn = get_db()
    conn.isolation_level = None   # manuálna transakcia (BEGIN/COMMIT/ROLLBACK)
    cur = conn.cursor()
    try:
        a = cur.execute("SELECT * FROM med_catalog WHERE id=?", (keep_id,)).fetchone()
        b = cur.execute("SELECT * FROM med_catalog WHERE id=?", (merge_id,)).fetchone()
        if not a or not b:
            raise ValueError("Liek nenájdený")
        a, b = dict(a), dict(b)

        # 1) vyriešené hodnoty polí (voľba keep/merge; inak auto: A ak má hodnotu, inak B)
        resolved = {}
        for f in _MERGE_FIELDS:
            ch = field_choices.get(f)
            if ch == "merge":
                resolved[f] = b[f]
            elif ch == "keep":
                resolved[f] = a[f]
            else:
                resolved[f] = a[f] if _has(a[f]) else b[f]
        if not _has(resolved["canonical_name"]):
            resolved["canonical_name"] = a["canonical_name"]

        # 2) aliasy: A + B + názov B, dedup; vynechaj výsledný kanonický názov
        try:
            aa = json.loads(a["aliases"] or "[]")
        except (ValueError, TypeError):
            aa = []
        try:
            ba = json.loads(b["aliases"] or "[]")
        except (ValueError, TypeError):
            ba = []
        aliases = _dedup_ci([*aa, *ba, b["canonical_name"]])
        canon_lc = resolved["canonical_name"].strip().lower()
        aliases = [x for x in aliases if x.strip().lower() != canon_lc]

        # 3) fotky: A + B (dedup), hlavná = main_photo | A.photo_path | prvá
        try:
            ap = json.loads(a["photos"] or "[]")
        except (ValueError, TypeError):
            ap = []
        try:
            bp = json.loads(b["photos"] or "[]")
        except (ValueError, TypeError):
            bp = []
        photos = []
        for p in [*ap, *bp]:
            if p and p not in photos:
                photos.append(p)
        photo_path = main_photo or a.get("photo_path") or (photos[0] if photos else None)
        if photo_path and photo_path not in photos:
            photos.insert(0, photo_path)

        now = datetime.utcnow().isoformat()
        cur.execute("BEGIN")
        b_events = cur.execute("SELECT COUNT(*) FROM events WHERE catalog_id=?", (merge_id,)).fetchone()[0]
        a_before = cur.execute("SELECT COUNT(*) FROM events WHERE catalog_id=?", (keep_id,)).fetchone()[0]

        # a) aktualizuj A
        cur.execute(
            """UPDATE med_catalog SET
               canonical_name=?, kind=?, strength=?, form=?, manufacturer=?, sukl_code=?,
               atc_code=?, description=?, side_effects=?, personal_notes=?, info_source=?,
               extracted_raw=?, pil_info=?, pil_source=?, aliases=?, photos=?, photo_path=?,
               updated_at=? WHERE id=?""",
            (resolved["canonical_name"], resolved["kind"], resolved["strength"], resolved["form"],
             resolved["manufacturer"], resolved["sukl_code"], resolved["atc_code"],
             resolved["description"], resolved["side_effects"], resolved["personal_notes"],
             resolved["info_source"], resolved["extracted_raw"], resolved["pil_info"],
             resolved["pil_source"], json.dumps(aliases, ensure_ascii=False),
             json.dumps(photos, ensure_ascii=False), photo_path, now, keep_id))

        # b) prepoj eventy B → A
        cur.execute("UPDATE events SET catalog_id=? WHERE catalog_id=?", (keep_id, merge_id))

        # d) over že nezostali osirené odkazy na B (inak NEMAŽ B → rollback)
        orphan = cur.execute("SELECT COUNT(*) FROM events WHERE catalog_id=?", (merge_id,)).fetchone()[0]
        if orphan != 0:
            raise RuntimeError(f"Zlúčenie zastavené: {orphan} osirených eventov — B sa nemaže")

        a_after = cur.execute("SELECT COUNT(*) FROM events WHERE catalog_id=?", (keep_id,)).fetchone()[0]

        # c) zmaž B (až po úspešnom prepojení)
        cur.execute("DELETE FROM med_catalog WHERE id=?", (merge_id,))

        cur.execute("COMMIT")
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        raise
    conn.close()

    summary = {
        "keep_id": keep_id, "merged_id": merge_id, "merged_name": b["canonical_name"],
        "moved_events": b_events, "a_events_before": a_before, "a_events_after": a_after,
        "aliases_count": len(aliases), "photos_count": len(photos),
    }
    # log (do stdout / journalctl) — bezpečnostný záznam čo sa zlúčilo
    print(f"[MERGE] keep={keep_id} merge={merge_id} ('{b['canonical_name']}') "
          f"moved_events={b_events} a_events {a_before}->{a_after} "
          f"aliases={len(aliases)} photos={len(photos)}", flush=True)
    return summary


def delete_catalog_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM med_catalog WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def set_catalog_active(item_id, active):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE med_catalog SET active=?, updated_at=? WHERE id=?",
                 (int(active), now, item_id))
    conn.commit()
    conn.close()


def find_by_alias(name):
    """Find a catalog item whose canonical_name or any alias matches `name`
    (case-insensitive, trimmed). Returns dict or None."""
    if not name:
        return None
    needle = name.strip().lower()
    if not needle:
        return None
    conn = get_db()
    rows = conn.execute("SELECT * FROM med_catalog WHERE active = 1").fetchall()
    conn.close()
    for row in rows:
        item = dict(row)
        if item["canonical_name"].strip().lower() == needle:
            return item
        try:
            aliases = json.loads(item.get("aliases") or "[]")
        except (ValueError, TypeError):
            aliases = []
        if any(str(a).strip().lower() == needle for a in aliases):
            return item
    return None


# ── LLM usage (tiché sledovanie spotreby tokenov / ceny) ─────────────────────

def log_usage(function, model, input_tokens, output_tokens, web_searches, cost_usd,
              user_id=None, context=None):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO llm_usage
           (created_at, function, model, input_tokens, output_tokens, web_searches,
            cost_usd, user_id, context)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (now, function, model, int(input_tokens or 0), int(output_tokens or 0),
         int(web_searches or 0), float(cost_usd or 0), user_id, context)
    )
    conn.commit()
    conn.close()


def _usage_where(period):
    """Vráti (where_sql, params) pre filter obdobia. period: day|month|all."""
    now = datetime.utcnow()
    if period == "day":
        return "WHERE created_at >= ?", [now.strftime("%Y-%m-%d")]
    if period == "month":
        return "WHERE created_at >= ?", [now.strftime("%Y-%m-01")]
    return "", []


def get_usage_totals(period="all"):
    where, params = _usage_where(period)
    conn = get_db()
    row = conn.execute(
        f"""SELECT COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(web_searches),0) AS web_searches,
                   COALESCE(SUM(cost_usd),0) AS cost_usd
            FROM llm_usage {where}""", params).fetchone()
    conn.close()
    return dict(row)


def get_usage_by_function(period="all"):
    where, params = _usage_where(period)
    conn = get_db()
    rows = conn.execute(
        f"""SELECT function,
                   COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(web_searches),0) AS web_searches,
                   COALESCE(SUM(cost_usd),0) AS cost_usd
            FROM llm_usage {where}
            GROUP BY function ORDER BY cost_usd DESC""", params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_usage(limit=30):
    conn = get_db()
    rows = conn.execute(
        """SELECT u.created_at, u.function, u.model, u.input_tokens, u.output_tokens,
                  u.web_searches, u.cost_usd, u.context, us.username AS author
           FROM llm_usage u LEFT JOIN users us ON u.user_id = us.id
           ORDER BY u.id DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
