"""
Pravidlá režimu liekov — deterministické, bez DB, bez LLM, bez siete.

JEDINÉ miesto v projekte, kde je definované, aké hodnoty smie mať
med_schedule.days a čo znamenajú. Importuje to main.py (validácia vstupu)
a bude to importovať porovnávač Fázy 2 (is_due). Zámerne bez závislosti
na database.py, aby sa dal testovať aj používať samostatne — rovnaký vzor
ako med_parser.py.

POZADIE (prečo tento modul vznikol):
Do 2026-08-05 UI /meds poznalo len tri tvary days. Riadok id23 (Vitamín
D3K2) mal v DB days='parny_datum' — hodnotu, ktorú getDaysValue() nevedela
zostaviť. Prvá editácia riadku cez /meds ju ticho prepísala na 'kazdy_den'
a pravidlo sa nenávratne stratilo. Server nemal žiadnu validáciu, takže
zápis prešiel bez chyby. Preto: povolené hodnoty na jednom mieste,
neznáma hodnota sa ODMIETNE, nikdy sa ticho nenahradí defaultom.
"""
from datetime import date as _date

KAZDY_DEN     = "kazdy_den"
PRI_KRIZE     = "pri_krize"
PARNY_DATUM   = "parny_datum"
NEPARNY_DATUM = "neparny_datum"

#: Kľúče dní v týždni. PORADIE JE VÝZNAMOVÉ — zodpovedá date.weekday()
#: (0 = pondelok … 6 = nedeľa). Nemeniť bez úpravy is_due().
WD_KEYS = ("po", "ut", "str", "stv", "pi", "so", "ne")

#: Hodnoty bez parametra (na rozdiel od CSV dní v týždni).
SIMPLE_DAYS = (KAZDY_DEN, PRI_KRIZE, PARNY_DATUM, NEPARNY_DATUM)

#: Slovenské štítky do UI. Zrkadlo DAYS_LABELS v static/meds.html —
#: pri zmene upraviť OBE miesta (projekt nemá build, JS sa nedá importovať).
DAYS_LABELS = {
    KAZDY_DEN:     "každý deň",
    PRI_KRIZE:     "pri kríze",
    PARNY_DATUM:   "každý párny dátum",
    NEPARNY_DATUM: "každý nepárny dátum",
}

WD_LABELS = {"po": "Po", "ut": "Ut", "str": "St", "stv": "Št",
             "pi": "Pi", "so": "So", "ne": "Ne"}


class InvalidDays(ValueError):
    """Neznáma / neplatná hodnota days.

    Zámerne výnimka, NIE tichý fallback na kazdy_den. Tichý fallback je
    presne tá chyba, kvôli ktorej tento modul existuje."""


def normalize_days(value):
    """Overí a znormalizuje hodnotu days. Vráti kanonický tvar.

    None aj prázdny reťazec sú CHYBA, nie 'kazdy_den' — chýbajúci vstup
    musí byť odlíšiteľný od platnej voľby "každý deň".

    CSV dní v týždni sa deduplikuje a zoradí podľa WD_KEYS, aby 'ut,po'
    a 'po,ut' boli jedna a tá istá hodnota.
    """
    if value is None:
        raise InvalidDays("days chýba (None)")
    v = str(value).strip()
    if not v:
        raise InvalidDays("days je prázdne")
    if v in SIMPLE_DAYS:
        return v
    keys = [k.strip() for k in v.split(",") if k.strip()]
    if keys and all(k in WD_KEYS for k in keys):
        seen = set(keys)
        return ",".join(k for k in WD_KEYS if k in seen)
    raise InvalidDays(f"neznáma hodnota days: {value!r}")


def is_due(days, on_date):
    """Má sa liek s týmto pravidlom brať v daný deň? Deterministické.

    `on_date` je datetime.date. Neznáme days → InvalidDays (NIE False —
    "nevieme" sa nesmie tváriť ako "nie", inak by Fáza 2 liek s pokazeným
    pravidlom ticho vynechala z porovnania).

    'pri_krize' → False: krízová (PRN) dávka nemá plánovaný deň, takže sa
    nikdy nesmie ohlásiť ako vynechaná.

    PÁRNY DÁTUM = deň v mesiaci, day % 2. Nie deň v roku, nie počet dní od
    kotvy. Dôsledok: po 31. nasleduje 1., čiže dva nepárne dni po sebe
    (7× do roka) a jednodňová diera pre párne pravidlo. JE TO ZÁMER —
    pravidlo, ktoré reálne platí, znie "pozri sa na dátum, je párny?",
    a softvér sa nesmie rozísť s človekom, ktorý drží dávkovač. Ozajstné
    striedanie ob deň by bola iná hodnota s kotviacim dátumom.
    """
    if not isinstance(on_date, _date):
        raise TypeError(f"on_date musí byť datetime.date, dostal {type(on_date).__name__}")
    v = normalize_days(days)
    if v == KAZDY_DEN:
        return True
    if v == PRI_KRIZE:
        return False
    if v == PARNY_DATUM:
        return on_date.day % 2 == 0
    if v == NEPARNY_DATUM:
        return on_date.day % 2 == 1
    return WD_KEYS[on_date.weekday()] in v.split(",")


def format_days(value):
    """Ľudský štítok. Neznámu hodnotu NEZAHADZUJE — vráti ju viditeľne
    označenú, aby sa v UI/logu dala rozpoznať namiesto tichého zmiznutia."""
    if value is None or not str(value).strip():
        return "neuvedené"
    v = str(value).strip()
    if v in DAYS_LABELS:
        return DAYS_LABELS[v]
    keys = [k.strip() for k in v.split(",") if k.strip()]
    if keys and all(k in WD_KEYS for k in keys):
        return ", ".join(WD_LABELS[k] for k in WD_KEYS if k in set(keys))
    return f"neznáme pravidlo: {v}"
