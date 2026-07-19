import base64
import io
import os
import json
import re
import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


SYSTEM_PROMPT = """Si asistent ktorý spracúva denníkové záznamy o zdravotnom stave dieťaťa.

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


MODEL_NAME = "claude-sonnet-4-6"


def extract_events(text: str, entry_date: str):
    client = _get_client()
    user_message = f"Dátum: {entry_date}\n\nText:\n{text}"

    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1536,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )

    raw = response.content[0].text.strip()

    result = _parse_llm_json(raw, text)

    if isinstance(result, list):
        events = result
        cleaned_text = text
    else:
        events = result.get("events", [])
        cleaned_text = result.get("cleaned_text", text)

    valid_types = {"liek", "nalada", "spravanie", "jedlo", "aktivita", "spatok", "fyzicke", "poznamka"}
    for ev in events:
        if ev.get("event_type") not in valid_types:
            ev["event_type"] = "poznamka"

    return events, cleaned_text, raw, MODEL_NAME


def _parse_llm_json(raw: str, fallback_text: str) -> dict | list:
    # 1. Priamy parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code fences a skús znova
    cleaned = raw
    if "```" in cleaned:
        cleaned = re.sub(r"```[a-z]*\n?", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Extrakcia prvého {...} alebo [...] bloku regexom
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pattern, cleaned)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

    # 4. Fallback — vráť prázdne eventy, pôvodný text
    return {"cleaned_text": fallback_text, "events": []}


def _resize_for_api(image_bytes: bytes, max_side: int = 1500) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


TRANSCRIBE_PROMPT = (
    "Prepíš rukou písaný text z obrázka. "
    "Jemne vyčisti: rozpíš skratky, oprav zjavné preklepy, zachovaj časové značky a štruktúru riadkov. "
    "Ak je na stránke dátum, vráť ho ako suggested_date vo formáte YYYY-MM-DD, inak null. "
    'Vráť iba JSON objekt bez iného textu: {"transcription": "...", "suggested_date": "YYYY-MM-DD alebo null"}'
)


SCAN_MED_PROMPT = (
    "Na obrázku (alebo na viacerých obrázkoch) je tá istá krabička lieku "
    "(alebo vitamínu/doplnku) — môžu to byť rôzne strany tej istej krabičky. "
    "Prečítaj text zo VŠETKÝCH obrázkov a ZLÚČ údaje do jedného výsledku "
    "(napr. názov z prednej strany, registračné číslo z bočnej, zloženie z ďalšej). "
    "ČÍTAJ IBA to, čo je reálne na obrázkoch — nič nedopĺňaj z vlastných "
    "znalostí, nehádaj. Ak údaj nie je čitateľný ani na jednom obrázku, vráť pre neho null.\n"
    "Polia:\n"
    "  name — názov lieku ako je na krabičke\n"
    "  strength — sila (napr. \"300 mg\", \"50 mg/ml\"), inak null\n"
    "  form — lieková forma (tableta/kapsula/kvapky/sirup/mast...), inak null\n"
    "  manufacturer — výrobca alebo držiteľ registrácie, inak null\n"
    "  sukl_code — ŠÚKL kód ak je viditeľný, inak null\n"
    "  atc_code — ATC kód ak je viditeľný, inak null\n"
    "  package_info — veľkosť balenia (napr. \"100 tabliet\"), inak null\n"
    "  extracted_all — JSON objekt s ďalšími čitateľnými údajmi z krabičky.\n"
    "\n"
    "PRE extracted_all POUŽI TIETO PEVNÉ SLOVENSKÉ KANONICKÉ KĽÚČE (ak je daný údaj na krabičke):\n"
    "  ucinna_latka — účinná / liečivá látka\n"
    "  zlozenie — zloženie / ingrediencie (VŠETKO do jedného kľúča: liečivá aj pomocné látky)\n"
    "  davkovanie — dávkovanie / užívanie / spôsob podania\n"
    "  upozornenia — VŠETKY upozornenia a varovania spolu ako JEDEN zoznam (pole reťazcov)\n"
    "  skladovanie — podmienky skladovania / uchovávania\n"
    "  exspiracia — dátum exspirácie / minimálna trvanlivosť\n"
    "  sarza — číslo šarže / LOT\n"
    "  ean — čiarový kód / EAN\n"
    "  reg_cislo — registračné číslo\n"
    "  vydaj — výdaj (na lekársky predpis / voľnopredajný)\n"
    "  typ_produktu — liek / doplnok / vitamín (ak je uvedené)\n"
    "\n"
    "PRAVIDLÁ pre extracted_all:\n"
    "  - Preferuj SLOVENSKÉ kanonické kľúče zo zoznamu vyššie.\n"
    "  - NEPRIDÁVAJ jazykové sufixy — žiadne _CZ, _SK, _fi, _de, _en. Jeden kľúč pre jeden koncept.\n"
    "  - Ak je údaj vo viacerých jazykoch, ZLÚČ do jedného kľúča (preferuj slovenčinu, inak čo je čitateľné).\n"
    "  - Údaje, ktoré NEPATRIA do žiadneho kľúča vyššie (napr. marketing/slogan, NRV/výživové hodnoty, "
    "diétne vlajky ako vegan, distribútor, web, recyklácia), daj do VNORENÉHO objektu pod kľúčom \"ostatne\".\n"
    "  - Ak je viac fotiek, zlúč údaje. Prázdny objekt {} ak nič ďalšie. Nečitateľné vynechaj.\n"
    "  - ČÍTAJ IBA reálny text z obrázkov — nič nedopĺňaj, nehádaj.\n"
    "\n"
    'Vráť iba JSON objekt bez iného textu: '
    '{"name": ..., "strength": ..., "form": ..., "manufacturer": ..., '
    '"sukl_code": ..., "atc_code": ..., "package_info": ..., '
    '"extracted_all": {"ucinna_latka": ..., "zlozenie": ..., "davkovanie": ..., '
    '"upozornenia": [...], "skladovanie": ..., "exspiracia": ..., "sarza": ..., '
    '"ean": ..., "reg_cislo": ..., "vydaj": ..., "typ_produktu": ..., "ostatne": {...}}}'
)

_SCAN_FIELDS = ("name", "strength", "form", "manufacturer", "sukl_code", "atc_code", "package_info")


def scan_med_package(images) -> dict:
    """Prečíta údaje z fotiek krabičky lieku cez Claude vision. Prijme jeden
    obrázok (bytes) alebo zoznam obrázkov (rôzne strany tej istej krabičky) —
    v jednom volaní ich zlúči. Číta len text z obrázkov — nečitateľné/chýbajúce
    polia vráti ako null (nehádaj)."""
    if isinstance(images, (bytes, bytearray)):
        images = [images]
    client = _get_client()

    content = []
    for img_bytes in images:
        resized = _resize_for_api(img_bytes)
        b64 = base64.standard_b64encode(resized).decode()
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": SCAN_MED_PROMPT})

    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1536,
        messages=[{"role": "user", "content": content}]
    )

    raw = response.content[0].text.strip()
    parsed = _parse_llm_json(raw, "")
    if not isinstance(parsed, dict):
        parsed = {}
    # normalizuj — len povolené polia, prázdne/"null" reťazce → None
    fields = {}
    for key in _SCAN_FIELDS:
        val = parsed.get(key)
        if isinstance(val, str):
            val = val.strip()
            if val == "" or val.lower() == "null":
                val = None
        fields[key] = val
    # neštruktúrované — všetko ostatné čitateľné z krabičky (voľná schéma)
    extracted = parsed.get("extracted_all")
    if not isinstance(extracted, dict):
        extracted = {}
    extracted = _clean_extracted(extracted)
    return {"fields": fields, "extracted_all": extracted, "raw": raw}


def _clean_extracted(obj):
    """Odstráni prázdne / null hodnoty z neštruktúrovaných dát, rekurzívne."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            cv = _clean_extracted(v)
            if cv not in (None, "", [], {}):
                out[k] = cv
        return out
    if isinstance(obj, list):
        cleaned = [_clean_extracted(v) for v in obj]
        return [v for v in cleaned if v not in (None, "", [], {})]
    if isinstance(obj, str):
        s = obj.strip()
        return None if s == "" or s.lower() == "null" else s
    return obj


def transcribe_photo(image_bytes: bytes) -> dict:
    client = _get_client()
    resized = _resize_for_api(image_bytes)
    b64 = base64.standard_b64encode(resized).decode()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": TRANSCRIBE_PROMPT},
            ]
        }]
    )

    raw = response.content[0].text.strip()
    result = _parse_llm_json(raw, "")
    suggested = result.get("suggested_date")
    if suggested in ("null", "", "YYYY-MM-DD alebo null"):
        suggested = None
    return {
        "transcription": result.get("transcription", raw),
        "suggested_date": suggested,
    }


if __name__ == "__main__":
    test_text = (
        "10:00 vstal, 10:15 dostal Orfiril a vitaminy, bol nervozny, "
        "12:17 pol tablety Tisercinu, 13:00 nervozny, 16:20 stolica, "
        "potom isiel pod papu"
    )
    entry_date = "2026-06-28"

    print("=== Surovy vystup LLM ===")
    events, cleaned_text, raw = extract_events(test_text, entry_date)
    print(raw)
    print("\n=== Cleaned text ===")
    print(cleaned_text)
    print("\n=== Parsovane eventy ===")
    for ev in events:
        t = ev.get("event_time") or "??:??"
        print(f"  [{t}] {ev['event_type']:12s} | {ev['value']}" +
              (f" | {ev['note']}" if ev.get("note") else ""))
    print(f"\nSpolu: {len(events)} eventov")
