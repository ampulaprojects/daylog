-- ============================================================================
-- Blok 2A — kódovanie striedavého režimu (days)
-- Vytvorené 2026-08-05. NESPUSTENÉ.
--
-- ⚠ PORADIE JE KRITICKÉ: NAJPRV KÓD, AŽ POTOM TOTO SQL.
--    Ak sa migrácia spustí pred nasadením kódu, ostane v DB hodnota, ktorú
--    /meds nepozná — a prvá editácia riadku ju zmaže presne tak, ako
--    2026-08-05 o 13:35 UTC. Postup:
--      1. nasadiť med_rules.py + main.py + static/meds.html
--      2. reštart daylog.service
--      3. overiť v prehliadači, že sa mód „Striedavo“ ponúka
--      4. až potom spustiť toto SQL
--      5. otvoriť riadok 23 v /meds, uložiť BEZ ZMENY, znova skontrolovať days
--         → to je jediný skutočný test pôvodnej chyby (JS testy neexistujú)
--
-- Spustenie:
--    cp /var/www/daylog/daylog.db /var/www/daylog/daylog.db.pre-daysrule
--    sqlite3 /var/www/daylog/daylog.db < migrate_med_days.sql
-- ============================================================================

-- ── KROK 1 — stav pred zmenou ──────────────────────────────────────────────
SELECT '--- PRED ---';
SELECT id, name, days, note, updated_at FROM med_schedule WHERE id IN (22, 23);


-- ── KROK 2 — migrácia ──────────────────────────────────────────────────────
BEGIN;

-- Vitamín D3K2 → párny dátum.
-- Doložené: CONTEXT.md (commit e209350, 2026-08-05T13:24:03Z) explicitne
-- uvádza, že tento riadok mal days='parny_datum'. O 13:35:05Z ho editácia
-- cez /meds prepísala na 'kazdy_den'. Toto je návrat pôvodnej hodnoty.
-- Guardy v WHERE: ak sa medzitým čokoľvek zmenilo, UPDATE neurobí NIČ,
-- namiesto toho aby prepísal nesprávny riadok. note zámerne nie je v SET.
UPDATE med_schedule
   SET days       = 'parny_datum',
       updated_at = '2026-08-05T20:00:00.000000'
 WHERE id   = 23
   AND name = 'Vitamín D3K2'
   AND days = 'kazdy_den';

-- ---------------------------------------------------------------------------
-- ZINOK — ZATIAĽ NEMIGROVAŤ. Čaká na overenie u opatrovateľky.
--
-- Dôvod: pre Zinok NEEXISTUJE doklad o pôvodnej hodnote. CONTEXT.md spomína
-- iba id23. Riadok 22 bol síce editovaný o minútu skôr (13:34:26Z) rovnakým
-- spôsobom, takže pravdepodobne dopadol rovnako — ale je to dohad, nie dôkaz.
-- Dáta sú slabé: 3 zo 4 pozorovaní sedia na nepárny deň (27.7. ✅, 28.7. ❌,
-- 1.8. ✅, 5.8. ✅). Na štyroch pozorovaniach to nestačí.
--
-- Po potvrdení odkomentovať a spustiť samostatne:
--
-- UPDATE med_schedule
--    SET days       = 'neparny_datum',
--        updated_at = '2026-08-05T20:00:00.000000'
--  WHERE id   = 22
--    AND name = 'Zinok'
--    AND days = 'kazdy_den';
-- ---------------------------------------------------------------------------

COMMIT;


-- ── KROK 3 — overenie ──────────────────────────────────────────────────────
SELECT '--- PO ---';
SELECT id, name, days, note, updated_at FROM med_schedule WHERE id IN (22, 23);
-- Očakávané: id23 days='parny_datum', note NEZMENENÁ
--            id22 days='kazdy_den'  (Zinok zatiaľ nemigrovaný)


-- ── KROK 4 — poistka: žiadna neočakávaná hodnota days ──────────────────────
SELECT '--- ROZLOŽENIE days ---';
SELECT days, COUNT(*) FROM med_schedule GROUP BY days ORDER BY days;
-- Očakávané:  kazdy_den 21 | parny_datum 1 | pri_krize 2
-- (po neskoršej migrácii Zinku: kazdy_den 20 | neparny_datum 1 | parny_datum 1 | pri_krize 2)
