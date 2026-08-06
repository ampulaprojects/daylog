#!/usr/bin/env python3
"""
Porovnanie STARÉHO a NOVÉHO SYSTEM_PROMPTu na reálnych textoch z produkcie.

PREČO: zmena promptu je najrizikovejšia časť Bloku 2B, lebo sa dotýka KAŽDEJ
extrakcie vrátane slovenských záznamov. Unit testy overia kód, nie správanie
modelu. Toto je jediný spôsob, ako zistiť, či sa novým promptom nezhoršila
extrakcia eventov zo slovenských textov — to je regresia, ktorú testy nechytia.

ČO ROBÍ:
  1. read-only vytiahne 5 reálnych záznamov z produkčnej DB (cez SSH alebo
     z lokálnej kópie, viď --db)
  2. každý text pošle DVAKRÁT — starým aj novým promptom
  3. vypíše výsledky vedľa seba: source_lang, jazyk cleaned_text, či vzniklo
     text_sk, počet a typy eventov, rozdiel v počte liekových eventov

ČO NEROBÍ:
  - NIČ nezapisuje do žiadnej DB (ani do llm_usage — volá API priamo)
  - nenasadzuje, nereštartuje, nemení súbory

SPUSTENIE (Jan, ručne — skript sa sám nespúšťa):
    python compare_prompts.py --db daylog.db.kopia
    python compare_prompts.py --db daylog.db.kopia --json vysledok.json

Náklady: 10 volaní Claude API (5 textov × 2 prompty), rádovo jednotky centov.
"""
import argparse
import json
import os
import sqlite3
import sys
import unicodedata

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import llm
from llm import MODEL_NAME, SYSTEM_PROMPT as NEW_PROMPT, _get_client, _parse_llm_json


# ── Starý prompt (stav pred Blokom 2B, commit c300e9c) ────────────────────────
# Overené ako BAJTOVO ZHODNÝ s `git show c300e9c:llm.py`. Ak si to overuješ sám,
# porovnávaj v Pythone s explicitným .decode('utf-8') — `git show | ...` cez
# Windows konzolu prepíše diakritiku na mojibake a ukáže falošný rozdiel.
OLD_PROMPT = """Si asistent ktorý spracúva denníkové záznamy o zdravotnom stave dieťaťa.

Vráť JSON objekt s dvoma poliami — žiadny iný text:

"cleaned_text": Opravená verzia vstupného textu. Oprav gramatiku, interpunkciu a chyby prepisu diktovania. Zachovaj všetky fakty a informácie. Ak je text v poriadku, vráť ho bez zmeny.

"events": Pole extrahovaných udalostí. Každý event:
  event_time — "HH:MM" alebo null
  event_type — "liek" | "nalada" | "spravanie" | "jedlo" | "aktivita" | "spatok" | "fyzicke" | "poznamka"
  value — popis max 60 znakov
  note — doplnok alebo null
  med_name — LEN pri type "liek": čistý názov lieku bez množstva (napr. "Orfiril", "Tisercin"). Pri ostatných typoch null.

Typy: liek=podanie lieku/vitamínov, nalada=emočný stav, spravanie=správanie/agresivita/stereotypy, jedlo=jedlo/pitie, aktivita=fyzická/sociálna aktivita, spatok=spánok/odpočinok, fyzicke=fyzické prejavy (stolica/zvracanie/teplota), poznamka=iné.

DÔLEŽITÉ pre lieky: Ak jeden záznam obsahuje VIAC liekov (napr. "3× Orfiril, 1/2 Tisercin, 1/4 Fevarin"), rozdeľ ich na SAMOSTATNÉ eventy typu "liek" — každý s rovnakým časom, každý len s JEDNÝM liekom. value obsahuje množstvo aj názov ("3× Orfiril"), med_name len názov ("Orfiril"). Vitamíny a doplnky rozdeľ rovnako.

Príklad výstupu:
{"cleaned_text": "...", "events": [{"event_time": "08:00", "event_type": "liek", "value": "3× Orfiril", "note": null, "med_name": "Orfiril"}, {"event_time": "08:00", "event_type": "liek", "value": "1/2 Tisercin", "note": null, "med_name": "Tisercin"}, {"event_time": "10:00", "event_type": "aktivita", "value": "vstal", "note": null, "med_name": null}]}"""


# ── Výber vzoriek (read-only) ────────────────────────────────────────────────
# Zámerne SQL, nie import database.py — nechceme spustiť init_db() a jeho
# ALTER TABLE nad kópiou produkcie.
SAMPLES_SQL = """
-- 2 ruské záznamy od Iriny (vrátane dnešného 2026-08-06)
SELECT e.id, e.entry_date, u.username, 'ru-irina' AS skupina, e.text
  FROM entries e JOIN users u ON e.user_id=u.id
 WHERE u.username='irina'
 ORDER BY e.entry_date DESC, e.id DESC LIMIT 2
"""

SAMPLES_SK_SQL = """
-- 2 slovenské záznamy (najdlhšie = najviac eventov na porovnanie)
SELECT e.id, e.entry_date, u.username, 'sk' AS skupina, e.text
  FROM entries e JOIN users u ON e.user_id=u.id
 WHERE u.username <> 'irina' AND length(e.text) > 200
 ORDER BY e.entry_date DESC LIMIT 2
"""

SAMPLES_FIX_SQL = """
-- 1 záznam, kde cleaned_text reálne opravoval gramatiku:
-- llm_analysis existuje a navrhnutý text sa LÍŠI od uloženého
SELECT e.id, e.entry_date, u.username, 'oprava-gramatiky' AS skupina, e.text
  FROM entries e JOIN users u ON e.user_id=u.id
 WHERE e.llm_analysis IS NOT NULL
   AND u.username <> 'irina'
   AND length(e.text) > 120
   AND instr(e.llm_analysis, '"cleaned_text"') > 0
 ORDER BY e.entry_date DESC LIMIT 1
"""


def load_samples(db_path):
    if not os.path.exists(db_path):
        sys.exit(f"DB neexistuje: {db_path}\n"
                 f"Sprav si read-only kópiu produkcie, napr.:\n"
                 f"  scp root@80.211.201.112:/var/www/daylog/daylog.db ./daylog.db.kopia")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = []
    for sql in (SAMPLES_SQL, SAMPLES_SK_SQL, SAMPLES_FIX_SQL):
        rows.extend(dict(r) for r in conn.execute(sql))
    conn.close()
    return rows


# ── Volanie modelu ───────────────────────────────────────────────────────────

def run_prompt(system_prompt, text, entry_date):
    """Jedno volanie. Zámerne NEVOLÁ _log_llm_usage — do llm_usage sa nič
    nezapisuje, skript je čisto čitateľský."""
    client = _get_client()
    resp = client.messages.create(
        model=MODEL_NAME, max_tokens=8192, system=system_prompt,
        messages=[{"role": "user", "content": f"Dátum: {entry_date}\n\nText:\n{text}"}],
    )
    raw = resp.content[0].text.strip()
    result = _parse_llm_json(raw, text)
    if isinstance(result, list):
        return {"events": result, "cleaned_text": text, "source_lang": None, "text_sk": None}
    return {
        "events": result.get("events", []) or [],
        "cleaned_text": result.get("cleaned_text") or "",
        "source_lang": result.get("source_lang"),
        "text_sk": result.get("text_sk"),
    }


# ── Heuristika jazyka (bez ďalšieho LLM volania) ─────────────────────────────

def script_of(s):
    """Podiel cyriliky vs latinky v texte — na overenie, či cleaned_text
    zostal v pôvodnom jazyku."""
    cyr = lat = 0
    for ch in s or "":
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "CYRILLIC" in name:
            cyr += 1
        elif "LATIN" in name:
            lat += 1
    total = cyr + lat
    if not total:
        return "?", 0.0
    return ("cyrilika" if cyr > lat else "latinka"), round(max(cyr, lat) / total, 3)


def summarize(res):
    types = {}
    for ev in res["events"]:
        t = ev.get("event_type", "?")
        types[t] = types.get(t, 0) + 1
    script, ratio = script_of(res["cleaned_text"])
    return {
        "source_lang": res.get("source_lang"),
        "cleaned_script": script,
        "cleaned_ratio": ratio,
        "cleaned_len": len(res["cleaned_text"] or ""),
        "text_sk": bool(res.get("text_sk")),
        "text_sk_len": len(res.get("text_sk") or ""),
        "events_total": len(res["events"]),
        "events_liek": types.get("liek", 0),
        "types": dict(sorted(types.items())),
    }


def main():
    ap = argparse.ArgumentParser(description="Porovnanie starého a nového SYSTEM_PROMPTu")
    ap.add_argument("--db", default="daylog.db",
                    help="cesta k (kópii) DB — otvára sa READ-ONLY")
    ap.add_argument("--json", help="uložiť plný výsledok do JSON súboru")
    args = ap.parse_args()

    samples = load_samples(args.db)
    if not samples:
        sys.exit("Z DB sa nevybrala ani jedna vzorka — skontroluj --db.")

    print(f"Model: {MODEL_NAME}")
    print(f"Vzoriek: {len(samples)}  →  {len(samples) * 2} volaní API\n")

    out = []
    regressions = []
    for s in samples:
        print("=" * 78)
        print(f"[{s['skupina']}] entry #{s['id']}  {s['entry_date']}  ({s['username']})")
        print(f"  text ({len(s['text'])} zn.): {s['text'][:110]!r}...")
        try:
            old = summarize(run_prompt(OLD_PROMPT, s["text"], s["entry_date"]))
            new = summarize(run_prompt(NEW_PROMPT, s["text"], s["entry_date"]))
        except Exception as e:
            print(f"  CHYBA volania: {e}")
            continue

        print(f"\n  {'':22} {'STARÝ':>26}   {'NOVÝ':>26}")
        for key, label in (("source_lang", "source_lang"),
                           ("cleaned_script", "cleaned_text písmo"),
                           ("cleaned_len", "cleaned_text dĺžka"),
                           ("text_sk", "text_sk vzniklo"),
                           ("text_sk_len", "text_sk dĺžka"),
                           ("events_total", "eventov spolu"),
                           ("events_liek", "z toho liek")):
            mark = "  <-- ZMENA" if old[key] != new[key] else ""
            print(f"  {label:22} {str(old[key]):>26}   {str(new[key]):>26}{mark}")
        print(f"  {'typy (starý)':22} {old['types']}")
        print(f"  {'typy (nový)':22} {new['types']}")

        # REGRESIA: slovenský text stratil eventy
        if s["skupina"] != "ru-irina" and new["events_total"] < old["events_total"]:
            regressions.append(
                f"entry #{s['id']} ({s['skupina']}): eventov {old['events_total']} → "
                f"{new['events_total']}, liek {old['events_liek']} → {new['events_liek']}")
        # KONTROLA: ruský cleaned_text musí zostať v cyrilike
        if s["skupina"] == "ru-irina" and new["cleaned_script"] != "cyrilika":
            regressions.append(
                f"entry #{s['id']}: nový cleaned_text NIE JE v cyrilike "
                f"({new['cleaned_script']}) — prompt neprestal prekladať")
        print()
        out.append({"entry_id": s["id"], "skupina": s["skupina"],
                    "entry_date": s["entry_date"], "stary": old, "novy": new})

    print("=" * 78)
    if regressions:
        print("!! POZOR — možná regresia:")
        for r in regressions:
            print(f"   - {r}")
    else:
        print("OK — žiadna strata eventov pri slovenských textoch, "
              "ruský cleaned_text zostal v cyrilike.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nPlný výsledok: {args.json}")


if __name__ == "__main__":
    main()
