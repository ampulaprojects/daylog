# daylog — kontext projektu

## Čo je daylog
Osobný denník pre zaznamenávanie udalostí a pozorovanie súvislostí.
Cieľ: zbierať čo najviac dát, hľadať vzory.

## Stack
- Zatiaľ neurčený

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

## Changelog

### 2026-06-27
- Autentifikácia: jan (admin) + katka (user), bcrypt, session cookie 30 dní
- /login, /logout, /profile (zmena hesla), /me endpoint
- Záznamy zdieľané (shared) s author attribution (LEFT JOIN users)
- nginx reverse proxy (port 80/443), uvicorn ako systemd služba
- Diktovanie hlasom: Web Speech API, sk-SK, toggle tlačidlo v UI
- HTTPS: Let's Encrypt certbot, doména daylog.bodk8.com, auto-renew

### 2026-06-26
- FastAPI beží na VPS ako systemd služba (daylog.service), auto-start po reboot
- API dostupné na http://80.211.201.112/
- Stack: Python 3.10 + FastAPI + SQLite + Vanilla JS
- Deploy workflow kompletný a otestovaný (git push → git pull na VPS)
