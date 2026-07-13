"""
Navrhovaný zoznam liekov pre med_schedule — vypíše, NEzapisuje.
Spusti: python seed_medications.py
Pre zápis do DB: python seed_medications.py --apply
"""
import sys

# name, kind, count(float), dose, unit, time_type, time_exact, time_value, days, note, sort_order
PROPOSED = [
    ("Orfiril",       "liek", 3.0,  "300mg", "tableta", None, "08:00", "ráno",      "kazdy_den",   None,                  10),
    ("Orfiril",       "liek", 3.0,  "300mg", "tableta", None, "18:00", "večer",     "kazdy_den",   None,                  11),
    ("Tisercin",      "liek", 0.5,  None,    "tableta", None, None,    "ráno",      "kazdy_den",   None,                  20),
    ("Tisercin",      "liek", 0.5,  None,    "tableta", None, None,    "večer",     "kazdy_den",   None,                  21),
    ("Tisercin",      "liek", 0.5,  None,    "tableta", None, None,    "pri kríze", "pri_krize",   "obsesívne stavy",     22),
    ("Fevarin",       "liek", 0.25, None,    "tableta", None, None,    "večer",     "kazdy_den",   "objavuje sa občas",   30),
    ("Chlorprotixen", "liek", 0.25, None,    "tableta", None, None,    "pri kríze", "pri_krize",   None,                  40),
]

COUNT_LABELS = {0.25: "1/4", 0.5: "1/2", 0.75: "3/4"}

def fmt_count(v):
    if v is None: return "-"
    return COUNT_LABELS.get(v, str(int(v) if v == int(v) else v))

COL = "{:<18} {:<8} {:<5} {:<8} {:<10} {:<8} {:<18} {:<14} {}"

def print_proposal():
    print("=" * 100)
    print("NAVRHOVANÝ ZOZNAM LIEKOV (seed_medications.py)")
    print("Skript NIČ NEZAPISUJE — over a uprav pred spustením --apply")
    print("=" * 100)
    print()
    print(COL.format("Názov", "Typ", "Kus", "Sila", "Jednotka", "Presný", "Čas", "Frekvencia", "Poznámka"))
    print("-" * 100)
    for name, kind, count, dose, unit, time_type, time_exact, time_value, days, note, sort_order in PROPOSED:
        print(COL.format(
            name, kind, fmt_count(count), dose or "-", unit or "-",
            time_exact or "-", time_value or "-", days, note or ""
        ))
    print()
    print(f"Celkom: {len(PROPOSED)} záznamov")
    print()
    print("Pre zápis spusti: python seed_medications.py --apply")

def apply_seed():
    from database import create_medication, get_medications
    existing = get_medications(include_inactive=True)
    if existing:
        print(f"POZOR: v tabuľke med_schedule už existuje {len(existing)} záznamov.")
        ans = input("Pokračovať a pridať seed záznamy? (ano/nie): ").strip().lower()
        if ans not in ("ano", "áno", "a", "y", "yes"):
            print("Zrušené.")
            return

    for name, kind, count, dose, unit, time_type, time_exact, time_value, days, note, sort_order in PROPOSED:
        med_id = create_medication(
            name=name, kind=kind, count=count, dose=dose, unit=unit,
            time_type=time_type, time_exact=time_exact, time_value=time_value,
            days=days, note=note, sort_order=sort_order
        )
        print(f"  Zapísaný id={med_id}: {name} {fmt_count(count)}x ({time_exact or time_value}, {days})")
    print(f"\nDokončené: {len(PROPOSED)} liekov zapísaných.")

if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_seed()
    else:
        print_proposal()
