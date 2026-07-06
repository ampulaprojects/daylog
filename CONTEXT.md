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

## Changelog

### 2026-07-06
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
