import base64
import io
import os
import json
import re
import urllib.request
import urllib.error
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


# ── Ceny (claude-sonnet-4-6) — jedno miesto, uprav keď sa zmenia ──────────────
PRICING = {
    "input_per_1m": 3.00,       # $ / 1M input tokenov
    "output_per_1m": 15.00,     # $ / 1M output tokenov
    "web_search_per_1000": 10.00,  # $ / 1000 web search dopytov
}


def compute_cost(input_tokens, output_tokens, web_searches=0):
    return (input_tokens / 1_000_000 * PRICING["input_per_1m"]
            + output_tokens / 1_000_000 * PRICING["output_per_1m"]
            + web_searches * PRICING["web_search_per_1000"] / 1000.0)


def _usage_from(resp_usage):
    """Normalizuj usage (SDK objekt alebo dict) → (input, output, web_searches)."""
    if resp_usage is None:
        return 0, 0, 0
    if not isinstance(resp_usage, dict):
        try:
            resp_usage = resp_usage.model_dump()
        except Exception:
            resp_usage = {
                "input_tokens": getattr(resp_usage, "input_tokens", 0),
                "output_tokens": getattr(resp_usage, "output_tokens", 0),
                "server_tool_use": getattr(resp_usage, "server_tool_use", None),
            }
    it = resp_usage.get("input_tokens") or 0
    ot = resp_usage.get("output_tokens") or 0
    stu = resp_usage.get("server_tool_use") or {}
    ws = (stu.get("web_search_requests") if isinstance(stu, dict) else 0) or 0
    return it, ot, ws


def _log_llm_usage(function, resp_usage, model=None, user_id=None, context=None):
    """Zaznamenaj spotrebu do DB. TICHO — chyba logovania nesmie zhodiť flow.
    Vráti {input_tokens, output_tokens, web_searches, cost_usd}."""
    it, ot, ws = _usage_from(resp_usage)
    cost = compute_cost(it, ot, ws)
    try:
        from database import log_usage
        log_usage(function=function, model=model or MODEL_NAME, input_tokens=it,
                  output_tokens=ot, web_searches=ws, cost_usd=cost,
                  user_id=user_id, context=context)
    except Exception:
        pass
    return {"input_tokens": it, "output_tokens": ot, "web_searches": ws, "cost_usd": cost}


def extract_events(text: str, entry_date: str, user_id=None):
    client = _get_client()
    user_message = f"Dátum: {entry_date}\n\nText:\n{text}"

    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1536,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )
    _log_llm_usage("extract", getattr(response, "usage", None), user_id=user_id)

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


def scan_med_package(images, user_id=None) -> dict:
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
    _log_llm_usage("scan", getattr(response, "usage", None), user_id=user_id)

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


def transcribe_photo(image_bytes: bytes, user_id=None) -> dict:
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
    _log_llm_usage("transcribe", getattr(response, "usage", None), user_id=user_id)

    raw = response.content[0].text.strip()
    result = _parse_llm_json(raw, "")
    suggested = result.get("suggested_date")
    if suggested in ("null", "", "YYYY-MM-DD alebo null"):
        suggested = None
    return {
        "transcription": result.get("transcription", raw),
        "suggested_date": suggested,
    }


# ── PIL dohľadávanie z webu (web_search server tool) ─────────────────────────
# Pozn.: pinnutá anthropic==0.40.0 nevie zostaviť web_search tool, preto ide
# fetch_pil_info cez raw HTTP (urllib). Ostatné volania používajú SDK ďalej.

_PIL_SYSTEM = (
    "Si asistent, ktorý dohľadáva oficiálne informácie o lieku z internetu. "
    "Používaš nástroj web_search. Extrahuj údaje IBA z reálne nájdeného oficiálneho "
    "dokumentu (príbalový leták PIL / SPC / oficiálna databáza). NEVYMÝŠĽAJ, nedopĺňaj "
    "z vlastnej pamäte. Ak nenájdeš dôveryhodný zdroj, vráť error a zdroj null."
)


def _pil_user_prompt(name, strength, manufacturer, atc_code):
    ident = ", ".join(p for p in [
        f"názov: {name}",
        f"sila: {strength}" if strength else "",
        f"výrobca/držiteľ: {manufacturer}" if manufacturer else "",
        f"ATC: {atc_code}" if atc_code else "",
    ] if p)
    return (
        f"Nájdi oficiálny príbalový leták (PIL/PIL) alebo SPC pre TENTO konkrétny liek:\n{ident}\n\n"
        "Preferuj oficiálne zdroje v tomto poradí: sukl.sk, ema.europa.eu, adc.sk, "
        "oficiálna stránka výrobcu. Over, že dokument zodpovedá práve tomuto lieku "
        "(názov + sila + výrobca).\n\n"
        "EFEKTÍVNOSŤ: Vyhľadávaj cielene, najviac 2–3 razy. Ak po pár vyhľadaniach na "
        "oficiálnych doménach nenájdeš relevantný dokument pre tento liek, NEHĽADAJ ďalej "
        "a vráť error (nižšie) — nezožer zbytočne veľa. Nečítaj viac dokumentov, než treba.\n\n"
        "Extrahuj informácie, ktoré typicky NIE SÚ na krabičke — najmä: úplné vedľajšie "
        "účinky, liekové interakcie, kontraindikácie, presné dávkovanie podľa veku/hmotnosti, "
        "dôležité upozornenia.\n\n"
        "Vráť NA KONCI iba jeden JSON objekt (bez ďalšieho textu okolo) s kanonickými "
        "slovenskými kľúčmi:\n"
        "  najdeny_liek — názov + sila lieku z nájdeného dokumentu (nech to používateľ overí)\n"
        "  vedlajsie_ucinky — zoznam alebo text\n"
        "  interakcie\n"
        "  kontraindikacie\n"
        "  davkovanie_detail — presné dávkovanie podľa veku/hmotnosti\n"
        "  upozornenia\n"
        "  (podľa dokumentu prípadne ďalšie kanonické kľúče, napr. sposob_uzivania, "
        "predavkovanie, tehotenstvo)\n"
        "  zdroj — POVINNÉ, URL dokumentu ktorý si reálne použil\n"
        "  zdroj_nazov — názov stránky/dokumentu\n\n"
        "Ak si NENAŠIEL spoľahlivý oficiálny zdroj pre tento liek, vráť namiesto toho:\n"
        '  {"error": "nenašiel som spoľahlivý zdroj", "zdroj": null}\n'
        "Bez reálneho zdroja (zdroj = URL) nič nevypĺňaj."
    )


class LLMApiError(RuntimeError):
    """Zrozumiteľná chyba z Anthropic API (čítaná z tela odpovede)."""


def _friendly_api_error(code, message):
    low = (message or "").lower()
    if "credit balance" in low:
        return "Nedostatok API kreditu — dobite kredit v Anthropic Console."
    if code == 429 or "rate limit" in low:
        return "API rate limit — skús o chvíľu znova."
    if code == 401 or "authentication" in low:
        return "Neplatný API kľúč."
    if code == 529 or "overloaded" in low:
        return "API je preťažené — skús o chvíľu znova."
    return f"API chyba {code}: {message}"


def _anthropic_http(payload: dict) -> dict:
    """Raw POST na /v1/messages — obchádza starú SDK kvôli web_search toolu.
    Pri chybe prečíta telo odpovede a vyhodí zrozumiteľnú hlášku (nie holé '400')."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8")
            message = json.loads(raw).get("error", {}).get("message") or raw
        except Exception:
            message = raw or str(e)
        raise LLMApiError(_friendly_api_error(e.code, message)) from None
    except urllib.error.URLError as e:
        raise LLMApiError(f"Sieťová chyba pri volaní API: {e.reason}") from None


def fetch_pil_info(name, strength=None, manufacturer=None, atc_code=None,
                   user_id=None, context=None) -> dict:
    """Dohľadá info z príbalového letáka cez web search. Vráti NÁVRH (neukladá).
    VŽDY vyžaduje zdroj (URL) — bez neho found=False. Súčasťou výsledku je aj
    'usage' (tokeny + cena tohto dohľadania) — PIL je jediná funkcia čo cenu ukazuje."""
    messages = [{"role": "user", "content": _pil_user_prompt(name, strength, manufacturer, atc_code)}]
    # basic web_search (bez dynamic-filtering code exec) + limit vyhľadávaní +
    # obmedzenie na oficiálne zdroje → rýchlejšie a bezpečnejšie (menšie riziko zlého zdroja)
    tools = [{
        "type": "web_search_20250305", "name": "web_search",
        "max_uses": 3,   # strop: bránime tomu, aby jeden PIL zožral 100k+ tokenov
        "allowed_domains": ["sukl.sk", "adc.sk", "ema.europa.eu", "adcc.sk"],
    }]

    raw_text = ""
    in_tok = out_tok = web_s = 0   # kumulatívne cez pause_turn iterácie
    for _ in range(4):
        data = _anthropic_http({
            "model": MODEL_NAME,
            "max_tokens": 4096,
            "system": _PIL_SYSTEM,
            "tools": tools,
            "messages": messages,
        })
        it, ot, ws = _usage_from(data.get("usage"))
        in_tok += it; out_tok += ot; web_s += ws
        content = data.get("content", [])
        raw_text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            continue
        break

    # zaznamenaj spotrebu (jeden riadok za celé dohľadanie) + priprav usage do výsledku
    cost = compute_cost(in_tok, out_tok, web_s)
    try:
        from database import log_usage
        log_usage(function="pil", model=MODEL_NAME, input_tokens=in_tok, output_tokens=out_tok,
                  web_searches=web_s, cost_usd=cost, user_id=user_id, context=context)
    except Exception:
        pass
    usage = {"input_tokens": in_tok, "output_tokens": out_tok,
             "web_searches": web_s, "cost_usd": cost}

    parsed = _parse_llm_json(raw_text, "")
    if not isinstance(parsed, dict):
        parsed = {}

    source_url = parsed.get("zdroj")
    if isinstance(source_url, str):
        source_url = source_url.strip() or None

    # bez reálneho zdroja alebo s errorom → nič sa neponúkne na uloženie
    if parsed.get("error") or not source_url:
        return {"found": False, "matched_medication": parsed.get("najdeny_liek"),
                "pil_info": {}, "source_url": None, "source_name": None,
                "usage": usage, "raw": raw_text}

    reserved = {"zdroj", "zdroj_nazov", "najdeny_liek", "error"}
    pil_info = _clean_extracted({k: v for k, v in parsed.items() if k not in reserved})

    return {
        "found": bool(pil_info),
        "matched_medication": parsed.get("najdeny_liek"),
        "pil_info": pil_info,
        "source_url": source_url,
        "source_name": parsed.get("zdroj_nazov"),
        "usage": usage,
        "raw": raw_text,
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
