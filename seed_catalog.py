"""
Navrhovaný katalóg liekov pre med_catalog — vypíše, NEzapisuje.
Spusti: python seed_catalog.py
Pre zápis do DB: python seed_catalog.py --apply

Aliasy vychádzajú z variantov mien, ktoré sa reálne objavujú v záznamoch
(chyby diktovania) — slúžia na budúcu normalizáciu názvov.
"""
import sys

# canonical_name, aliases[], kind, strength, form, manufacturer, atc_code
PROPOSED = [
    ("Orfiril Long",
        ["Orfiril", "Ofriril", "Ofriliril", "Ofrilril", "Ofriliral", "Ofrilirit"],
        "liek", "300mg", "tableta", "Desitin", "N03AG01"),
    ("Tisercin",
        ["Tisercin", "Tisercinu", "tesakcín"],
        "liek", None, "tableta", None, "N05AA02"),
    ("Fevarin",
        ["Fevarin"],
        "liek", None, "tableta", None, "N06AB08"),
    ("Chlorprothixen",
        ["Chlorprotixen", "Chlorprothixen"],
        "liek", None, "tableta", None, "N05AF03"),
    ("Srdcín",
        ["Srdcín"],
        "liek", None, None, None, None),
    ("Karbozín",
        ["Karbozín"],
        "doplnok", None, None, None, None),
]


def print_proposal():
    print("=" * 90)
    print("NAVRHOVANÝ KATALÓG LIEKOV (seed_catalog.py)")
    print("Skript NIČ NEZAPISUJE — over a uprav pred spustením --apply")
    print("=" * 90)
    print()
    col = "{:<18} {:<9} {:<8} {:<10} {:<11} {}"
    print(col.format("Kanonický názov", "Typ", "Sila", "ATC", "Výrobca", "Aliasy"))
    print("-" * 90)
    for name, aliases, kind, strength, form, manufacturer, atc in PROPOSED:
        print(col.format(
            name, kind, strength or "-", atc or "-", manufacturer or "-",
            ", ".join(aliases)
        ))
    print()
    print(f"Celkom: {len(PROPOSED)} položiek, "
          f"{sum(len(a) for _, a, *_ in PROPOSED)} aliasov")
    print()
    print("Pre zápis spusti: python seed_catalog.py --apply")


def apply_seed():
    import json
    from database import create_catalog_item, get_catalog
    existing = get_catalog(include_inactive=True)
    if existing:
        print(f"POZOR: v tabuľke med_catalog už existuje {len(existing)} položiek.")
        ans = input("Pokračovať a pridať seed položky? (ano/nie): ").strip().lower()
        if ans not in ("ano", "áno", "a", "y", "yes"):
            print("Zrušené.")
            return

    for name, aliases, kind, strength, form, manufacturer, atc in PROPOSED:
        item_id = create_catalog_item(
            canonical_name=name, aliases=json.dumps(aliases), kind=kind,
            strength=strength, form=form, manufacturer=manufacturer,
            atc_code=atc, info_source="manual (seed)"
        )
        print(f"  Zapísaný id={item_id}: {name} ({len(aliases)} aliasov)")
    print(f"\nDokončené: {len(PROPOSED)} položiek zapísaných.")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_seed()
    else:
        print_proposal()
