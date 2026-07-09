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

- Vlastný Google Cloud client_id pre rclone (shared client_id sa v 2026 vypína)
- OneDrive ako druhý backup cieľ
- Režim liekov (medications/routines) — odložené, počkať na viac dát
- llm_* audit zápis (rozhodnuté, neimplementované)
- Anomálny záznam entry_date 2020-06-27 (opraviť/zmazať)

## Changelog

### 2026-07-09
- Zálohovanie: rclone + Google Drive, denná automatická šifrovaná záloha (cron 03:00 UTC)
- backup.sh: SQLite .backup snapshot + tar (DB + uploads) + GPG AES256 + rclone upload + rotácia 14 dní
- GPG passphrase v /etc/daylog-backup.pass (heslo uložené v Bitwardene)
- Restore overený: obnovená DB má identické počty ako živá, fotky prítomné
- TODO: vlastný Google client_id (shared sa v 2026 odstavuje), druhý cieľ OneDrive ako poistka

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
