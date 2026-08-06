"""
Všeobecné (nešpecifikované) výrazy pre lieky + stavy prekladu záznamu.

JEDINÉ miesto v projekte, kde je definované, ktorý raw_name znamená
"nejaký liek, nevieme aký". Importuje to main.py (označenie eventov pri
extrakcii) a database.py (rozvinutie generického riadku na konkrétne).
Bez DB, bez LLM, bez siete — rovnaký vzor ako med_rules.py a med_parser.py.

POZADIE:
Opatrovateľka Irina píše po rusky a lieky často bez názvu — "12:15-лекарство".
LLM to preloží na "liek", parser z toho vyrobí riadok event_meds s
catalog_id = NULL. Taký riadok sa nedá priradiť k žiadnemu slotu režimu,
takže by budúci denný sumár hlásil nepotvrdené dávky, ktoré podané boli.

DÔLEŽITÉ — kritérium je ZHODA SO ZOZNAMOM, nie absencia catalog_id:
riadky ako "Magm", "Mg", "Vit. C" alebo cyrilické "karнозín" tiež nemajú
catalog_id, ale to je problém aliasov a preklepov. Ten tento modul NERIEŠI
a označiť ich ako "nešpecifikované" by bolo nesprávne — používateľ tam
napísal konkrétny liek, len ho appka nerozpoznala.

Porovnáva sa CELÝ normalizovaný raw_name, nie podreťazec. Podreťazcové
párovanie je pri krátkych výrazoch nebezpečné (viď CONTEXT.md: alias "Mg"
by pároval aj v "600 mg Orfiril").
"""
import unicodedata

# ── Stavy prekladu záznamu ────────────────────────────────────────────────
# NULL v DB = starý záznam, extrakcia s prekladom nikdy nebežala. Zámerne
# odlíšené od TR_ZLYHAL — "nevieme" sa nesmie tváriť ako "zlyhalo".
TR_NEPOTREBNY = "nepotrebny"   # text už bol po slovensky, text_sk je NULL
TR_HOTOVY     = "hotovy"       # preložené, text_sk vyplnené
TR_ZLYHAL     = "zlyhal"       # cudzí jazyk rozpoznaný, ale preklad neprišiel

TRANSLATION_STATUSES = (TR_NEPOTREBNY, TR_HOTOVY, TR_ZLYHAL)

TRANSLATION_LABELS = {
    TR_NEPOTREBNY: "netreba prekladať",
    TR_HOTOVY:     "preložené",
    TR_ZLYHAL:     "preklad zlyhal",
}


class InvalidTranslationStatus(ValueError):
    """Neznámy stav prekladu. Zámerne výnimka, nie tichý fallback."""


def normalize_status(value):
    """None prejde (starý záznam / extrakcia nebežala). Neznáma neprázdna
    hodnota je chyba — tichý prepis na default je presne to, čo nechceme."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if v not in TRANSLATION_STATUSES:
        raise InvalidTranslationStatus(f"neznámy stav prekladu: {value!r}")
    return v


# ── Normalizácia názvov ───────────────────────────────────────────────────

def normalize_term(value):
    """casefold + zahodenie diakritiky + zbalenie medzier.

    'Vitamíny', 'VITAMÍNY', 'vitaminy' aj '  vitamíny  ' padnú na 'vitaminy'.
    Cyrilika prejde bez ujmy (NFD ju rozkladá len minimálne).
    None/'' → '' (nikdy nespadne, volá sa nad surovými dátami z LLM).
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFD", str(value).strip().casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split())


# Zoznam je v UŽ NORMALIZOVANEJ podobe (bez diakritiky, malé písmená) —
# test_12 to stráži. Slovenské tvary sú doložené z produkcie (audit 2026-08-06),
# ruské sú poistka pre prípad, že LLM nepreloží.
GENERIC_MED_TERMS = frozenset({
    # ── slovenské ────────────────────────────────────────────────────────
    "liek", "lieky", "lieku", "liekov",
    "podanie lieku", "podanie liekov",
    "lieky podla zoznamu", "liek podla zoznamu",
    "vitamin", "vitaminy", "vitaminov",
    "tabletka", "tabletky", "tabliet",
    "doplnok", "doplnky", "doplnkov",
    "kvapky", "kapsula", "kapsuly",
    # ── ruské (Irina) ────────────────────────────────────────────────────
    "лекарство", "лекарства", "лекарств",
    "лекарство по списку", "лекарства по списку",
    "витамин", "витамины", "витаминов",
    "таблетка", "таблетки", "таблеток",
    "капли", "капсула", "капсулы",
    "добавка", "добавки",
})


def is_generic(raw_name):
    """Je to všeobecný výraz ("nejaký liek"), nie konkrétny názov?

    Zhoda CELÉHO normalizovaného reťazca so zoznamom. Zámerne NIE podreťazec
    a zámerne NIE "chýba catalog_id".

    Vedomé rozhodnutie: 'Ranné vitamíny podané až na obed' sa NEZHODUJE.
    Fakticky je to všeobecný výraz, ale ako celok v zozname nie je a
    podreťazcové párovanie sem nepustíme. Je to jediný taký riadok,
    historický, z migrácie.
    """
    return normalize_term(raw_name) in GENERIC_MED_TERMS
