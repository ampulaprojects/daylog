"""
Preskenuje fotky existujúcich liekov v katalógu novým (konsolidovaným) vision
promptom a AKTUALIZUJE LEN extracted_raw. Štruktúrované polia (canonical_name,
strength, aliases, personal_notes, ...) sa NEMENIA — mohol ich upraviť používateľ.

Spusti (len plán, nič nemení):   python rescan_catalog.py
Zápis do DB:                     python rescan_catalog.py --apply

Fotky musia fyzicky existovať v uploads/. Liek bez dostupných fotiek sa preskočí.
extracted_raw sa PREPÍŠE novým výstupom (to je zámer — konsolidácia kľúčov).
"""
import os
import sys
import json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))

from database import get_catalog, get_db
from llm import scan_med_package


def photo_list(item):
    """Vráti zoznam ciest k fotkám lieku (photos JSON, fallback photo_path)."""
    try:
        photos = json.loads(item.get("photos") or "[]")
    except (ValueError, TypeError):
        photos = []
    if not photos and item.get("photo_path"):
        photos = [item["photo_path"]]
    return photos


def resolve(path):
    """uploads/xxx.jpg → absolútna cesta k súboru (relatívne k projektu)."""
    return os.path.join(BASE, path)


def count_keys(raw):
    try:
        obj = json.loads(raw or "{}")
        return len(obj) if isinstance(obj, dict) else 0
    except (ValueError, TypeError):
        return 0


def update_extracted_raw(item_id, extracted_raw):
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE med_catalog SET extracted_raw=?, updated_at=? WHERE id=?",
                 (extracted_raw, now, item_id))
    conn.commit()
    conn.close()


def main():
    apply = "--apply" in sys.argv
    items = get_catalog(include_inactive=True)

    print("=" * 78)
    print("RESCAN KATALÓGU — konsolidácia extracted_raw novým vision promptom")
    print("Režim:", "ZÁPIS (--apply)" if apply else "len plán (bez --apply nič nemení)")
    print("=" * 78)

    with_photos = []
    skipped_no_photos = []
    for it in items:
        photos = photo_list(it)
        if photos:
            with_photos.append((it, photos))
        # lieky bez fotiek nemá zmysel skenovať — ticho ignoruj (nie sú kandidáti)

    print(f"\nLiekov v katalógu: {len(items)} | s fotkami: {len(with_photos)}\n")

    processed = 0
    skipped_missing = 0
    for it, photos in with_photos:
        name = it["canonical_name"]
        existing = [p for p in photos if os.path.isfile(resolve(p))]
        missing = [p for p in photos if not os.path.isfile(resolve(p))]

        print("-" * 78)
        print(f"[id={it['id']}] {name}")
        print(f"  fotiek v DB: {len(photos)} | dostupných: {len(existing)}"
              + (f" | CHÝBAJÚ: {len(missing)}" if missing else ""))
        for m in missing:
            print(f"    chýba súbor: {m}")

        if not existing:
            print("  → PRESKOČENÉ (žiadna dostupná fotka)")
            skipped_missing += 1
            continue

        before = count_keys(it.get("extracted_raw"))

        if not apply:
            print(f"  → by sa preskenovalo z {len(existing)} fotiek "
                  f"(teraz extracted_raw má {before} kľúčov)")
            processed += 1
            continue

        # --apply: reálny sken
        try:
            images = []
            for p in existing:
                with open(resolve(p), "rb") as f:
                    images.append(f.read())
            result = scan_med_package(images)
            extracted = result.get("extracted_all") or {}
            new_raw = json.dumps(extracted, ensure_ascii=False) if extracted else None
            after = len(extracted)
            update_extracted_raw(it["id"], new_raw)
            keys = ", ".join(list(extracted.keys())[:12])
            print(f"  → PREPÍSANÉ: kľúčov {before} → {after}")
            print(f"    nové kľúče: {keys}")
            processed += 1
        except Exception as e:
            print(f"  → CHYBA skenu: {e}")

    print("\n" + "=" * 78)
    verb = "spracovaných" if apply else "na spracovanie"
    print(f"Hotovo: {processed} {verb}, {skipped_missing} preskočených (chýbajúce fotky).")
    if not apply:
        print("Pre reálny zápis spusti: python rescan_catalog.py --apply")


if __name__ == "__main__":
    main()
