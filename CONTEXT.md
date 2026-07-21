# daylog — kontext projektu

## Čo je daylog
Osobný denník pre zaznamenávanie udalostí a pozorovanie súvislostí.
Cieľ: zbierať čo najviac dát, hľadať vzory.

## Stack
- Backend: Python 3.10, FastAPI, SQLite (sqlite3, bez ORM), uvicorn
- Frontend: Vanilla JS, Web Speech API (sk-SK), bez frameworku
- Auth: bcrypt 4.1.3, itsdangerous (session cookie, 30 dní)
- LLM: Anthropic Claude API (claude-sonnet-4-6), python-dotenv
- Infra: nginx reverse proxy, systemd (daylog.service), Let's Encrypt HTTPS

## VPS
- Poskytovateľ: Forpsi
- IP: 80.211.201.112
- OS: Ubuntu 22.04 LTS
- Prístup: SSH kľúč (ed25519), user root

## GitHub
- Repo: https://github.com/ampulaprojects/daylog
- Viditeľnosť: private

## Lokálny vývoj
- Cesta: C:\Users\jan.tupek\projects\daylog
- VS Code + GitHub autorizácia cez token

## Workflow
- Vývoj lokálne v C:\Users\jan.tupek\projects\daylog
- Deploy: .\deploy.ps1 -msg "popis zmeny"
  - commitne a pushne na GitHub
  - VPS si stiahne zmeny cez git pull
- VPS repo: /var/www/daylog (naklonované z GitHub cez deploy key)

## SSH
- Lokál → VPS: SSH kľúč ed25519 (~/.ssh/id_ed25519), user root
- VPS → GitHub: deploy key (~/.ssh/daylog_deploy), read-only, len pre repo daylog

## Architektúra

### Databáza (SQLite)
- `users`: id, username, hashed_password, role, created_at
- `entries`: id, created_at, entry_date, entry_time, text, source, user_id + LLM polia
- `events`: id, entry_id, user_id, event_time, event_type, value, note, confirmed, created_at

### LLM flow
1. `POST /entries/extract` — zavolá Claude API, vráti `events` + `cleaned_text`, nič nezapíše do DB
2. Používateľ skontroluje/upraví eventy v review paneli
3. `POST /entries/confirm` — zapíše entry + events do DB

### Event typy
`liek`, `nalada`, `spravanie`, `jedlo`, `aktivita`, `spatok`, `fyzicke`, `poznamka`

### Kľúčové súbory
- `main.py` — FastAPI endpointy
- `database.py` — SQLite funkcie (bez ORM)
- `llm.py` — Claude API volanie + parsovanie JSON
- `auth.py` — bcrypt + session token
- `static/index.html` — celé UI (single page)
- `deploy.ps1` — git commit + push + SSH git pull na VPS

### API kľúč (ANTHROPIC_API_KEY)
- Lokálne: `.env` súbor (v .gitignore)
- VPS: systemd drop-in `/etc/systemd/system/daylog.service.d/env.conf`

## Otvorené úlohy

- OneDrive ako druhý backup cieľ
- Režim liekov Fáza 1 (evidencia) — HOTOVÉ; Fáza 2 (porovnanie eventov s režimom, detekcia odchýlok) — odložené
- Napojenie /catalog/lookup na LLM extrakciu eventov (normalizácia názvov pri zápise)
- Trvalé testy: integrita katalógu hotová (tests/test_catalog_integrity.py); API testy (tests/test_api.py) stále chýbajú. Pozor: pytest nie je nainštalovaný, testy sú preto spustiteľné aj cez `python tests/test_catalog_integrity.py`
- datetime.utcnow() deprecated — nahradiť pri väčšom zásahu do database.py
- Zvážiť EAN ako atomické pole (zatiaľ v extracted_raw)
- Poznámka: vzorka katalógu je zaťažená doplnkami; pre liekové rozhodnutia dôležité reálne SK lieky syna
- PRAGMA foreign_keys nie je zapnutá; FK nie sú za behu vynucované, events.catalog_id a med_schedule.catalog_id nemajú ani REFERENCES. Integrita visí len na aplikačnej logike. Zapnutie vyžaduje rozhodnutie o ON DELETE a prestavbu tabuliek
- POST /entries/confirm nie je atomické — entry a eventy sa zapisujú cez samostatné spojenia a commity; pri zlyhaní zostane neúplný záznam
- Fáza 2 liekov naráža na dátový model: jeden event často obsahuje viac liekov naraz (napr. "3× Ofriril, 1/2 Tisercin, 1/4 Fevarin"), ale catalog_id je jediná hodnota. Z 507 eventov je 104 typu liek, z toho len 37 má catalog_id. Treba rozhodnúť medzi rozbitím eventov na jeden liek = jeden event, alebo spojovacou tabuľkou event_meds
- V dátach sú cyrilické homoglyfy z diktovania (Ofriлril, Tisercinу) — párovanie cez aliasy ich musí normalizovať; riešiteľné deterministicky, bez LLM

## Poznámky / pasce

- Windows git neukladá execute bit — pri nových shell skriptoch treba `git update-index --chmod=+x` pred commitom
- ŠÚKL: kategorizačný zoznam (data.gov.sk) obsahuje len hradené lieky — Tisercin tam nie je. Plný register + PIL sú za JavaScript SPA (vyžadovalo by Playwright). Preto katalóg staviame na foto+vision, nie na registri.
- anthropic SDK je pinnutý na 0.40.0 (staré) — web_search tool nevie zostaviť, preto fetch_pil_info ide cez raw HTTP. Upgrade SDK je samostatná budúca úloha (dotkne sa jadra — extrakcia/sken/prepis — treba pretestovať).
- Dlhé LLM volania (web search ~95s) potrebujú nginx proxy_read_timeout — default 60s nestačí
- Web search vkladá plný text nájdených stránok do input tokenov — jeden PIL ~40-98k tokenov. Preto cache + limit web searchov.
- PIL nájde len reálne SK lieky (sukl.sk/adc.sk); zahraničné doplnky poctivo vráti "nenašiel" — to je zámer (poistka proti halucinácii)
- Zlučovanie liekov je nezvratné a dotýka sa eventov (catalog_id) — poradie kritické: prepoj eventy → over 0 osirených → až potom zmaž duplikát. Pred zlúčením na produkcii sa robí záloha daylog.db.pre-merge.
- Dual-mode endpoint (HTML/JSON na jednej URL podľa Accept) potrebuje Vary: Accept, inak prehliadač cacheuje jednu podobu a zamieňa ich; čistejšie je oddeliť JSON API na vlastnú URL.
- Osirené odkazy vznikajú ticho, lebo SQLite bez PRAGMA foreign_keys nič nekontroluje. Každá nová väzba cez *_id potrebuje vlastnú aplikačnú ochranu, kým nie je FK enforcement zapnutý.
- Lokálny .env musí obsahovať DAYLOG_SECRET (aspoň 32 znakov) aj DAYLOG_INSECURE_COOKIE=1, inak sa appka buď nespustí, alebo sa cez http://localhost nedá prihlásiť (secure cookie prehliadač po HTTP nepošle). Na VPS .env NIE JE — secure preto ostáva zapnutý a DAYLOG_SECRET ide zo systemd drop-inu
- deploy.ps1 NEREŠTARTUJE daylog.service — po každom deploy treba ručne `systemctl restart daylog`, inak beží starý kód.

## Changelog

### 2026-07-21
- Blok 2A — fail-fast na secrete: auth.py už nemá žiadny fallback. Pri importe overí DAYLOG_SECRET (resolve_secret) a pri chýbajúcom, prázdnom, kratšom než 32 znakov alebo starom "daylog-dev-secret-2026" vyhodí SecretConfigError so SK návodom (hodnotu secretu nikdy nevypisuje). Padá pri ŠTARTE, nie až pri prihlásení — auth sa importuje z main.py, takže uvicorn ani nenabehne
- auth.py teraz sám volá load_dotenv() — importuje sa skôr než llm.py, ktorý dovtedy .env načítaval ako prvý
- Session cookie má secure=True ako default; vypína ho len explicitné DAYLOG_INSECURE_COOKIE=1 (lokálny http://localhost). Atribúty cookie sú na jednom mieste: auth.set_session_cookie() / clear_session_cookie(), main.py ich len volá. Duplicitné SESSION_MAX_AGE z main.py odstránené
- tests/test_auth_secret.py (8 testov): validácia hodnoty + reálne odmietnutie štartu v samostatnom procese. Test "chýbajúci secret" beží nad kópiou auth.py v temp adresári, lebo projektový .env by inak secret dodal
- Security: DAYLOG_SECRET nastavený na VPS cez systemd drop-in env.conf. Príčina: auth.py mal fallback "daylog-dev-secret-2026", ktorý je v gite — session cookie sa dala sfalšovať a prihlásiť sa ako ľubovoľný používateľ. Reštart zneplatnil všetky staré session (očakávané, nutné znovu prihlásenie). Otvorené: auth.py by mal pri chýbajúcom secrete odmietnuť štart, cookie nemá secure=True
- Integrita katalógu: delete_catalog_item() už nedovolí zmazať položku, na ktorú odkazujú eventy alebo režim — vráti HTTP 409 s počtami a odporučí zlúčenie. Príčina: holý DELETE bez kontroly väzieb vyrobil na produkcii 4 osirené eventy. Únikový východ pre používateľa zostáva deaktivácia (active=0)
- merge_catalog_items() prepája okrem events.catalog_id aj med_schedule.catalog_id, v tej istej transakcii, a kontrola osirených beží nad OBOMA tabuľkami. Príčina: merge vznikol pred med_schedule.catalog_id a o režime nevedel — 9 z 11 riadkov režimu má catalog_id, takže zlúčenie použitej položky by ticho rozbilo režim. Kontrola porovnáva PRÍRASTOK osirených, nie absolútny počet (absolútna kontrola by zablokovala každý merge kvôli cudzej, staršej chybe v dátach)
- Oprava dát na produkcii: fix_orphan_events.py (dry-run default, --apply na zápis, mapovanie v ORPHAN_MAP) prepojil 4 osirené eventy Orfiril z neexistujúceho catalog_id=1 na id=8 (Orfiril long). Pred spustením záloha daylog.db.pre-orphanfix-2. Po oprave 0 osirených v events aj med_schedule, integrity_check ok
- Prvé trvalé testy: tests/test_catalog_integrity.py (6 testov, vlastná dočasná DB cez tempfile, nikdy sa nedotknú daylog.db). Pokrývajú blokovanie delete, merge vrátane med_schedule, 0 osirených po merge, dry-run nič nezapíše
- UI fix: deleteItem v static/catalog.html hlásil "Zmazané" pri každom HTTP statuse — tichý úspech by zatajil aj novú 409-ku
- Diagnostika odhalila, že lokálna a produkčná DB sa výrazne líšia (produkcia 109 entries / 507 events / 11 katalóg vs lokál 8 / 30 / 9). Závery z lokálnej DB neplatia pre produkciu

### 2026-07-20
- Fix: prázdny dropdown "Z katalógu" v /meds — JSON zoznam katalógu presunutý na samostatný GET /catalog/list (vždy JSON, Cache-Control: no-store), defenzívne Vary: Accept na dual-mode /catalog
- Príčina: prehliadač vracal zacacheovaný HTML namiesto JSON (chýbal Vary: Accept na /catalog), r.json() zlyhal, tichý catch → prázdny dropdown
- Frontend fetche (meds, index, catalog) presmerované na /catalog/list; loadCatalogList má console.error namiesto tichého catchu
- Prepojenie všetkých troch vrstiev cez catalog_id: katalóg (čo je liek) ↔ med_schedule (kedy sa má brať) ↔ events (kedy sa reálne bral) — pridanie lieku do režimu výberom z katalógu

### 2026-07-19
- Zlučovanie duplicitných liekov v katalógu: POST /catalog/merge, porovnávací panel s výberom polí, transakčné (celé alebo nič), prepojí eventy z B na A PRED zmazaním B, overí 0 osirených odkazov (inak rollback), zlúči aliasy + fotky; B sa natvrdo zmaže
- PIL cache neúspechu: pil_last_attempt stĺpec — neúspešné hľadanie sa zapamätá, tlačidlo ukáže "Zdroj sa nenašiel (dátum)", opakovanie vyžaduje potvrdenie (nešpiní peniaze náhodným klikom); úspech resetuje príznak
- Sledovanie spotreby LLM: tabuľka llm_usage, tiché zbieranie pri každom volaní (input/output tokeny, web searches, cena)
- Stránka /usage (len admin): rozpad podľa funkcie (extract/scan/transcribe/pil), súčty za deň/mesiac/celkovo, posledné volania
- Ceny natvrdo v config (claude-sonnet-4-6: $3/1M input, $15/1M output, web search $10/1000) — upraviteľné
- PIL cena viditeľná v paneli dohľadávania; PIL ~34× drahšie než extrakcia ($0.169 vs $0.005)
- PIL optimalizácia: cache (raz dohľadané = uložené, "Znovu dohľadať" funguje), limit web searchov (max 3, efektívnostná inštrukcia) → -56% tokenov
- Oprava error handling v _anthropic_http: číta telo chybovej odpovede, zrozumiteľná hláška (napr. "Nedostatok API kreditu") namiesto holého "400"
- Katalóg Krok B: dohľadávanie z príbalového letáka (PIL) cez web search — tlačidlo v detaile lieku, LEN na manuálny pokyn
- Web search cez raw HTTP (urllib) v fetch_pil_info — obídený starý anthropic SDK 0.40.0 (nevie web_search tool), existujúce LLM volania nedotknuté (jedna premenná naraz)
- Poistky: obmedzené na oficiálne zdroje (sukl.sk, adc.sk, ema.europa.eu), povinný zdroj (URL), len návrh na potvrdenie, disclaimer v UI, nehalucinuje (bez zdroja → nič)
- pil_info + pil_source stĺpce — oddelené od extracted_raw (z krabičky); dve samostatné sekcie v detaile
- nginx proxy_read_timeout zvýšený na 180s (fetch-pil trvá ~95s, default 60s by prerezal)

### 2026-07-17
- Katalóg Krok 2: foto krabičky + Claude vision autofill (prečíta názov, silu, formu, výrobcu, kódy; nečitateľné = null, nič nevymýšľa)
- Viac fotiek na liek (galéria, rôzne strany krabičky), vision zlučuje info zo všetkých; hlavná fotka = thumbnail; výber z galérie/PC aj kamery (odstránený capture)
- extracted_raw: neštruktúrované úložisko všetkých čitateľných údajov z krabičky (bottom-up)
- Konsolidácia vision kľúčov na pevný SK slovník (ucinna_latka, zlozenie, davkovanie, upozornenia, skladovanie, exspiracia, sarza, ean, reg_cislo, vydaj, typ_produktu); marketing/NRV → "ostatne"; žiadne jazykové sufixy
- rescan_catalog.py: preskenovanie existujúcich liekov novým promptom (--apply, len extracted_raw, štruktúrované polia nedotknuté)
- Analýza 9 liekov ukázala fragmentáciu (110 kľúčov, 19 variantov "zloženia") → konsolidácia znížila na 7-11 kľúčov/liek zo slovníka

### 2026-07-15
- Katalóg liekov (/catalog): referenčná príručka liekov s aliasmi pre normalizáciu názvov z diktovania (Orfiril/Ofriril/Ofriliril → Orfiril Long)
- Tabuľka med_catalog: canonical_name, aliases (JSON), kind, strength, form, manufacturer, sukl_code, atc_code, description, side_effects, personal_notes (pozorovania u syna, oddelene od generického popisu), info_source, photo_path
- GET /catalog/lookup?name=X — normalizácia názvov (case-insensitive, trim, len aktívne)
- Seed: 6 liekov / 14 aliasov z reálnych dát
- Security: odstránené natvrdo zadané heslá z main.py (_init_users), vytváranie užívateľov presunuté do manage_users.py add-user; produkčné heslá zmenené
- Chore: .gitattributes — koniec CRLF warningov

### 2026-07-13
- Sekcia Lieky (/meds): editovateľný režim liekov syna, odkaz v hlavičke
- Tabuľka med_schedule: name, kind (liek/vitamín/doplnok), count (REAL), dose, unit, time_exact (HH:MM) + time_value (popis), days (kazdy_den/pri_krize/konkrétne dni), note, active, sort_order
- UI: pridať/upraviť (inline na mieste)/deaktivovať/zmazať, množstvo cez tlačidlá (¼-3) + číselné pole, frekvencia s výberom dní (Po-Ne), drag & drop poradie (SortableJS + šípky fallback)
- Seed: 7 liekov z reálnych dát (Orfiril, Tisercin, Fevarin, Chlorprotixen) s normalizovanými názvami
- Fáza 2 (porovnanie so skutočnými eventmi) odložená

### 2026-07-12
- Desktop layout: dvojzónový responzívny (formulár vľavo sticky, záznamy + ovládače vpravo), max-šírka 1300px; mobil ostáva jednostĺpcový cez media query
- Fix: backup.sh stratil execute bit pri git pull (Windows git neukladá +x) — nastavené natrvalo cez git update-index --chmod=+x; cron backup preto medzi 10.-12.7. nebežal
- Fix: duplicitné logovanie backupu (tee + cron redirect) — cron už nepresmerováva stdout, stderr ide do daylog-backup-cron-err.log
- Overené: cron reálne spúšťa backup (test o 19:57 UTC)

### 2026-07-10
- llm_* audit: pri LLM extrakcii sa ukladá llm_model, llm_analysis (surová odpoveď), llm_processed_at; priamy zápis bez LLM ponecháva NULL
- MODEL_NAME konštanta v llm.py (názov modelu na jednom mieste)
- Oprava anomálie: záznam #20 mal zle prečítaný rok z fotky (2020→2026)

### 2026-07-09
- Zálohovanie: rclone + Google Drive, denná automatická šifrovaná záloha (cron 03:00 UTC)
- backup.sh: SQLite .backup snapshot + tar (DB + uploads) + GPG AES256 + rclone upload + rotácia 14 dní
- GPG passphrase v /etc/daylog-backup.pass (heslo uložené v Bitwardene)
- Restore overený: obnovená DB má identické počty ako živá, fotky prítomné
- TODO: vlastný Google client_id (shared sa v 2026 odstavuje), druhý cieľ OneDrive ako poistka
- Vlastný Google Cloud client_id pre rclone (OAuth desktop app, projekt My First Project, publikované do produkcie) — odstránená závislosť na shared client_id ktorý Google vypína v 2026. Client secret uložený v rclone config na VPS.
- Zálohovanie rozdelené: DB šifrovane (gdrive:daylog-backups/, 14 dní rotácia), fotky samostatne cez rclone copy (gdrive:daylog-photos/, nešifrované, len prírastky, nikdy nemaže)
- Dôvod: fotky sú veľké a nemenné, netreba ich denne re-šifrovať; sú aj tak už na Drive z telefónu

### 2026-07-08
- Git hygiena: daylog.db a *.backup odstránené z celej git histórie (filter-repo + force push), .gitignore doplnený
- UI Dávka 1: auto-výška textarea na mobile, event grid layout (typ+čas hore, popis a note pod)
- UI Dávka 2: inline editor — editácia záznamu sa rozbalí na mieste, nie na vrchu stránky
- UI Dávka 3: "Prepočítať eventy z textu" — LLM sa volá len na explicitný pokyn (text a eventy ako oddelené vrstvy)
- Fotky: thumbnaily v zozname, autentifikovaný /photos endpoint (path traversal ochrana), fotka viditeľná počas editácie prepisu
- Časy: normalizované na HH:MM (migrácia existujúcich dát), native time picker
- Rozhodnutie: llm_* stĺpce sa budú ukladať (audit); confirmed flag zatiaľ nechaný, rozhodne sa neskôr
- DB: 3 users, 30 entries, ~147 events
- Organizácia záznamov: prepínač zobrazenia (Podľa dní / Zoznam), prepínač triedenia (dátum udalosti / dátum zápisu)
- Filtre: podľa typu eventu (režim Celé záznamy / Len eventy), podľa rozsahu dátumov, počítadlo výsledkov, zbaliteľný panel

### 2026-07-06
- Čistá DB migrácia v2: odstránené mŕtve stĺpce (title, mood, tags, llm_*) a migračné artefakty
- Zmazaný register.html a update_llm_analysis() (mŕtvy kód)
- Overené na mobile: editácia eventov v review paneli, cleaned text s Prijať/Zamietnuť
- Databáza: 3 users, 10 entries, 47 events
- Ďalší krok: tabuľka režimu liekov (medications/routines)
- Robustné parsovanie JSON z LLM v `llm.py`
- `_parse_llm_json()`: 4 fallback úrovne (priamy parse → strip markdown → regex extrakcia → prázdne eventy)
- LLM chyba už nikdy nespadne celú extrakciu, vráti prázdne eventy a pôvodný text

### 2026-06-27 (večer)
- Doména: daylog.bodk8.com → 80.211.201.112 (DNS Websupport)
- HTTPS: Let's Encrypt certifikát cez certbot
- nginx config aktualizovaný pre SSL
- Diktovanie hlasom funkčné na mobile (vyžaduje HTTPS)
- Ďalší krok: skenovanie fotiek/textu z papiera

### 2026-06-27
- Autentifikácia: jan (admin) + katka (user), bcrypt, session cookie 30 dní
- /login, /logout, /profile (zmena hesla), /me endpoint
- Záznamy zdieľané (shared) s author attribution (LEFT JOIN users)
- nginx reverse proxy (port 80/443), uvicorn ako systemd služba
- Diktovanie hlasom: Web Speech API, sk-SK, toggle tlačidlo v UI
- HTTPS: Let's Encrypt certbot, doména daylog.bodk8.com, auto-renew
- Nová databázová schéma: entries + events tabuľky
- LLM extrakcia eventov: Claude API (`claude-sonnet-4-6`), review UI, confirm flow
- Edit/delete existujúcich záznamov
- Editovateľné eventy v review paneli (inputy + select pre typ)
- Oprava CSS: specificity bug pre time input, background shorthand zabíjal SVG šípku v select
- Čas ako text input (namiesto type=time) — intuitívnejší na Windows

### 2026-06-26
- FastAPI beží na VPS ako systemd služba (daylog.service), auto-start po reboot
- API dostupné na http://80.211.201.112/
- Stack: Python 3.10 + FastAPI + SQLite + Vanilla JS
- Deploy workflow kompletný a otestovaný (git push → git pull na VPS)
