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

Typy: liek=podanie lieku/vitamínov, nalada=emočný stav, spravanie=správanie/agresivita/stereotypy, jedlo=jedlo/pitie, aktivita=fyzická/sociálna aktivita, spatok=spánok/odpočinok, fyzicke=fyzické prejavy (stolica/zvracanie/teplota), poznamka=iné.

Príklad výstupu:
{"cleaned_text": "...", "events": [{"event_time": "08:00", "event_type": "aktivita", "value": "vstal", "note": null}]}"""


def extract_events(text: str, entry_date: str):
    client = _get_client()
    user_message = f"Dátum: {entry_date}\n\nText:\n{text}"

    response = client.messages.create(
        model="claude-sonnet-4-6",
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

    return events, cleaned_text, raw


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
