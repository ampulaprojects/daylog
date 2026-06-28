import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


SYSTEM_PROMPT = """Si asistent ktorý extrahuje štruktúrované eventy z denníkového záznamu o zdravotnom stave dieťaťa.

Z textu extrahuj všetky udalosti a vráť ich ako JSON pole. Každý event má tieto polia:
- event_time: čas vo formáte "HH:MM" alebo null ak nie je uvedený
- event_type: jeden z typov: "liek", "nalada", "spravanie", "jedlo", "aktivita", "spatok", "fyzicke", "poznamka"
- value: stručný popis udalosti (max 60 znakov)
- note: doplňujúca informácia alebo null

Typy:
- liek: podanie lieku alebo vitamínov
- nalada: emočný stav (nervózny, spokojný, plakal, smial sa...)
- spravanie: správanie (agresivita, sebapoškodzovanie, stereotypy...)
- jedlo: jedlo alebo pitie
- aktivita: fyzická alebo sociálna aktivita
- spatok: spánok, zdriemnutie, odpočinok
- fyzicke: fyzické prejavy (stolica, zvracanie, teplota, bolesti...)
- poznamka: čokoľvek iné čo nespadá do ostatných kategórií

Vráť IBA JSON pole, žiadny iný text. Príklad formátu:
[
  {"event_time": "08:00", "event_type": "aktivita", "value": "vstal", "note": null},
  {"event_time": "08:15", "event_type": "liek", "value": "Orfiril 300mg", "note": "bez problémov"}
]"""


def extract_events(text: str, entry_date: str) -> list[dict]:
    client = _get_client()
    user_message = f"Dátum záznamu: {entry_date}\n\nText záznamu:\n{text}"

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )

    raw = response.content[0].text.strip()
    # strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    events = json.loads(raw)
    valid_types = {"liek", "nalada", "spravanie", "jedlo", "aktivita", "spatok", "fyzicke", "poznamka"}
    for ev in events:
        if ev.get("event_type") not in valid_types:
            ev["event_type"] = "poznamka"
    return events, raw


if __name__ == "__main__":
    test_text = (
        "10:00 vstal, 10:15 dostal Orfiril a vitamíny, bol nervózny, "
        "12:17 pol tablety Tisercinu, 13:00 nervózny, 16:20 stolica, "
        "potom išiel pod papuču"
    )
    entry_date = "2026-06-28"

    print("=== Surový výstup LLM ===")
    events, raw = extract_events(test_text, entry_date)
    print(raw)
    print("\n=== Parsované eventy ===")
    for ev in events:
        t = ev.get("event_time") or "??:??"
        print(f"  [{t}] {ev['event_type']:12s} | {ev['value']}" +
              (f" | {ev['note']}" if ev.get("note") else ""))
    print(f"\nSpolu: {len(events)} eventov")
