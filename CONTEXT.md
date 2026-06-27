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

## Stav / posledný krok
- [2026-06-27] Deploy workflow kompletný a otestovaný
- Ďalší krok: začať stavať aplikáciu daylog
