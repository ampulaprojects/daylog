#!/usr/bin/env python3
"""
Doplnenie chýbajúcich aliasov k EXISTUJÚCIM položkám katalógu (Blok 3A.5).

Dry-run je default — bez --apply skript LEN reportuje a DB otvára read-only.
So --apply zapisuje v jednej transakcii (BEGIN/COMMIT/ROLLBACK), rovnaký
vzor ako merge_catalog_items().

Rozsah: IBA aliasy k položkám, ktoré v katalógu už sú. Nové položky sa
NEVYTVÁRAJÚ — doplnky mimo katalógu sa len vypíšu ako kandidáti.

Spusti:  python3 add_aliases.py --db /var/www/daylog/daylog.db
         python3 add_aliases.py --db /var/www/daylog/daylog.db --apply
"""
import argparse
import difflib
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_med_events import (load_catalog, match_catalog, norm, parse_event)

# ── NÁVRH ALIASOV ──────────────────────────────────────────────────────────
# Pridávajú sa LEN tvary, ktoré sú jednoznačne danou položkou. Čokoľvek, čo
# vyžaduje úsudok ("Magnetit je asi Magtein"), patrí do DISPUTED nižšie.
NEW_ALIASES = {
    8:  ["Orifiril", "Orifril"],   # Orfiril long — preklepy z diktovania (Orfiril↔Orifiril)
    9:  ["Karbazín"],              # Carnosine Younger — už má Karbozín/Karnozín, toto je ďalší tvar
    # Magtein Magnesium L-Threonat — skrátený tvar kanonického názvu + diktačný
    # tvar "Magnetit" (potvrdil Jan z vlastnej znalosti, 2026-07-22)
    12: ["Magtein", "Magnetit"],
    13: ["B6"],                    # Vitamin B6 — v dátach sa píše výlučne "B6"
    14: ["Tiamín"],                # Thiamin — slovenský prepis (Thiamin sa páruje, Tiamín nie)
}

# Tvary, ktoré ZÁMERNE NEPRIDÁVAM. Vypíšu sa v reporte na rozhodnutie.
DISPUTED = {
    "magnézium tripla": ("11? Tripla Magnesium",
                         "Poradie slov sedí na 'Tripla Magnesium', ale je to voľný opis, "
                         "nie názov. Riziko zámeny s Magteinom (tiež magnézium)."),
    "magnézium": ("11? alebo 12?",
                  "KOLÍZIA: v katalógu sú dve magnéziové položky (Tripla Magnesium, "
                  "Magtein). Holé 'magnézium' sa nedá priradiť jednoznačne."),
    "mozog": ("10? Grasgevoerd Brein",
              "'Brein' = mozog, čiže domáca prezývka dáva zmysel. Ale je to preklad, "
              "nie tvar názvu — a 'mozog bx' / 'vitamíny: mozog' môžu byť aj iný prípravok."),
    "Magm": ("12? alebo 11?", "Zrejme skratka pri diktovaní. Nedá sa určiť, ktorý magnézium."),
    "BX": ("?", "Nejasné — pravdepodobne B-komplex, ktorý v katalógu nie je."),
    "C": ("?", "Vitamín C nie je v katalógu; jednopísmenový alias by navyše "
               "chytal náhodné výskyty v texte."),
}

KEY_MEDS = {8: "Orfiril long", 16: "TISERCIN", 17: "Fevarin", 4: "Chlorprothixen"}


def out(s=""):
    print(s)


def h1(t):
    out(); out("=" * 92); out(t); out("=" * 92)


def h2(t):
    out(); out("── " + t + " " + "─" * max(0, 88 - len(t)))


def open_db(path, readonly=True):
    if not os.path.exists(path):
        sys.exit(f"CHYBA: databáza neexistuje: {path}")
    if readonly:
        conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def existing_aliases(conn):
    """{id: (canonical_name, [aliasy])}"""
    out_ = {}
    for r in conn.execute("SELECT id, canonical_name, aliases FROM med_catalog ORDER BY id"):
        try:
            al = json.loads(r["aliases"] or "[]")
        except (ValueError, TypeError):
            al = []
        out_[r["id"]] = (r["canonical_name"], al)
    return out_


def check_collision(alias, target_id, catalog):
    """Vráti dôvod kolízie, alebo None. Alias nesmie ukazovať na inú položku."""
    a = norm(alias)
    if not a:
        return "prázdny alias"
    for it in catalog:
        if it["id"] == target_id:
            continue
        for v in it["variants"]:
            if not v:
                continue
            if a == v or a in v or v in a:
                return f"koliduje s #{it['id']} {it['name']!r} (variant {v!r})"
    return None


def build_plan(catalog, mapping, current):
    """(plan, skipped) — plan: {id: [nové aliasy]}, skipped: [(id, alias, dôvod)]"""
    by_id = {it["id"]: it for it in catalog}
    plan, skipped = {}, []
    for cid, aliases in mapping.items():
        if cid not in by_id:
            skipped += [(cid, a, "položka katalógu neexistuje") for a in aliases]
            continue
        have = {norm(x) for x in ([current[cid][0]] + current[cid][1])}
        for a in aliases:
            if norm(a) in have:
                skipped.append((cid, a, "alias už existuje (po normalizácii)"))
                continue
            reason = check_collision(a, cid, catalog)
            if reason:
                skipped.append((cid, a, reason))
                continue
            plan.setdefault(cid, []).append(a)
            have.add(norm(a))
    return plan, skipped


def catalog_with_plan(catalog, plan):
    """Kópia katalógu s domyslenými novými aliasmi — na hypotetické párovanie."""
    new = []
    for it in catalog:
        extra = [norm(a) for a in plan.get(it["id"], [])]
        variants = sorted(set(it["variants"]) | set(extra), key=len, reverse=True)
        new.append({"id": it["id"], "name": it["name"], "variants": variants})
    return new


def collect_rows(conn, catalog):
    """Všetky navrhované riadky event_meds (rovnaký rozklad ako parser)."""
    rows = []
    for e in conn.execute(
            "SELECT ev.id, e.entry_date, ev.value, ev.note FROM events ev "
            "JOIN entries e ON ev.entry_id = e.id WHERE ev.event_type = 'liek' "
            "ORDER BY e.entry_date, ev.id"):
        parsed, _ = parse_event(e["value"], e["note"], catalog)
        for r in parsed:
            rows.append({"event_id": e["id"], "date": e["entry_date"],
                         "raw_name": r["raw_name"], "catalog_id": r["catalog_id"]})
    return rows


def apply_plan(path, plan):
    """Zapíše aliasy v JEDNEJ transakcii. Vráti {id: počet pridaných}."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None          # manuálna transakcia
    cur = conn.cursor()
    added = {}
    try:
        cur.execute("BEGIN")
        now = datetime.utcnow().isoformat()
        for cid, aliases in plan.items():
            row = cur.execute("SELECT canonical_name, aliases FROM med_catalog WHERE id=?",
                              (cid,)).fetchone()
            if not row:
                raise ValueError(f"položka katalógu #{cid} neexistuje")
            try:
                cur_al = json.loads(row["aliases"] or "[]")
            except (ValueError, TypeError):
                cur_al = []
            have = {norm(x) for x in ([row["canonical_name"]] + cur_al)}
            new = [a for a in aliases if norm(a) not in have]   # idempotencia
            if not new:
                added[cid] = 0
                continue
            merged = cur_al + new
            cur.execute("UPDATE med_catalog SET aliases=?, updated_at=? WHERE id=?",
                        (json.dumps(merged, ensure_ascii=False), now, cid))
            added[cid] = len(new)
        cur.execute("COMMIT")
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        raise
    conn.close()
    return added


def main():
    ap = argparse.ArgumentParser(description="Doplnenie aliasov k existujúcim položkám katalógu")
    ap.add_argument("--db", default="daylog.db")
    ap.add_argument("--apply", action="store_true", help="zapísať (inak iba dry-run)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    conn = open_db(args.db, readonly=True)
    catalog = load_catalog(conn)
    current = existing_aliases(conn)
    rows_before = collect_rows(conn, catalog)

    plan, skipped = build_plan(catalog, NEW_ALIASES, current)
    cat_after = catalog_with_plan(catalog, plan)
    rows_after = [dict(r, catalog_id=match_catalog(r["raw_name"], cat_after))
                  for r in rows_before]
    conn.close()

    mode = "APPLY (zapisuje sa)" if args.apply else "DRY-RUN (nič sa nezapisuje)"
    out(f"DB: {args.db}   režim: {mode}")
    out(f"Navrhovaných riadkov event_meds: {len(rows_before)}")

    # ── 1. diagnostika nespárovaných ────────────────────────────────────
    unmatched = [r for r in rows_before if not r["catalog_id"]]
    h1("1. DIAGNOSTIKA NESPÁROVANÝCH TVAROV")
    out(f"Nespárovaných riadkov PRED: {len(unmatched)} zo {len(rows_before)}")

    group_a = Counter()
    group_b = Counter()
    for r in unmatched:
        after = match_catalog(r["raw_name"], cat_after)
        (group_a if after else group_b)[r["raw_name"]] += 1

    h2("(a) tvary, ktoré po pridaní aliasov PATRIA k existujúcej položke")
    if not group_a:
        out("   žiadne")
    for nm, n in group_a.most_common():
        cid = match_catalog(nm, cat_after)
        out(f"   {n:>3}×  {nm!r:<38} → #{cid} {current[cid][0]!r}")

    h2("(b) KANDIDÁTI NA NOVÉ POLOŽKY KATALÓGU — MIMO ROZSAH, NEROBÍM S NIMI NIČ")
    for nm, n in group_b.most_common():
        out(f"   {n:>3}×  {nm!r}")

    # ── 2. plán ─────────────────────────────────────────────────────────
    h1("2. PLÁN ZMIEN (po položkách)")
    if not plan:
        out("Žiadne aliasy na pridanie.")
    for cid in sorted(plan):
        name, al = current[cid]
        out(f"\n  #{cid} {name!r}")
        out(f"      súčasné aliasy : {al}")
        out(f"      PRIDAŤ         : {plan[cid]}")
    out(f"\n  Spolu: {sum(len(v) for v in plan.values())} nových aliasov "
        f"k {len(plan)} položkám")

    h2("Vynechané pri kontrole (duplicita / kolízia)")
    if not skipped:
        out("   žiadne")
    for cid, a, why in skipped:
        out(f"   #{cid} {a!r}: {why}")

    h2("SPORNÉ TVARY — ZÁMERNE NEPRIDANÉ, ROZHODNI TY")
    for form, (guess, why) in DISPUTED.items():
        cnt = sum(n for nm, n in group_b.items() if norm(form) in norm(nm))
        out(f"   {form!r}  (v dátach ~{cnt}×)  tip: {guess}")
        out(f"      {why}")

    # ── 3. dopad ────────────────────────────────────────────────────────
    h1("3. DOPAD NA PÁROVANIE")
    before_ok = sum(1 for r in rows_before if r["catalog_id"])
    after_ok = sum(1 for r in rows_after if r["catalog_id"])
    n = max(len(rows_before), 1)
    out(f"   spárované PRED : {before_ok}/{len(rows_before)}  ({100.0 * before_ok / n:.1f} %)")
    out(f"   spárované PO   : {after_ok}/{len(rows_after)}  ({100.0 * after_ok / n:.1f} %)")
    out(f"   novo spárovaných riadkov: {after_ok - before_ok}")
    ev_before = {r["event_id"] for r in rows_before if r["catalog_id"]}
    ev_after = {r["event_id"] for r in rows_after if r["catalog_id"]}
    out(f"   eventov, ktoré po zmene majú aspoň jeden spárovaný liek: "
        f"{len(ev_before)} → {len(ev_after)}")

    # ── 4. kľúčové lieky ────────────────────────────────────────────────
    h1("4. KONTROLA 4 KĽÚČOVÝCH LIEKOV")
    key_variants = {}
    for cid, name in KEY_MEDS.items():
        it = next((x for x in cat_after if x["id"] == cid), None)
        key_variants[cid] = it["variants"] if it else []
    still = []
    for r in rows_after:
        if r["catalog_id"]:
            continue
        t = norm(r["raw_name"])
        for tok in t.replace("/", " ").split():
            if len(tok) < 4 or tok.isdigit():
                continue
            for cid, variants in key_variants.items():
                for v in variants:
                    if difflib.SequenceMatcher(None, tok, v).ratio() >= 0.72:
                        still.append((r, cid, tok, v))
                        break
    for cid, name in KEY_MEDS.items():
        cnt = sum(1 for r in rows_after if r["catalog_id"] == cid)
        out(f"   #{cid} {name:<18} spárovaných riadkov po zmene: {cnt}")
    h2("Tvary podobné kľúčovým liekom, ktoré by ZOSTALI nespárované (kritické pre Fázu 2)")
    if not still:
        out("   ŽIADNE — všetky diktované tvary 4 kľúčových liekov sú po pridaní aliasov spárované.")
    for r, cid, tok, v in still:
        out(f"   event #{r['event_id']} {r['date']} {r['raw_name']!r} "
            f"— token {tok!r} ~ {v!r} (#{cid})")

    # ── zápis ───────────────────────────────────────────────────────────
    h1("ZÁPIS")
    if not args.apply:
        out("DRY-RUN — nič sa nezapísalo. Na zápis spusti ten istý príkaz s --apply.")
        return
    added = apply_plan(args.db, plan)
    for cid, n_ in sorted(added.items()):
        out(f"   #{cid} {current[cid][0]!r}: pridaných {n_} aliasov")
    out(f"   Spolu zapísaných: {sum(added.values())}")


if __name__ == "__main__":
    main()
