#!/usr/bin/env bash
set -euo pipefail

# == Config ==
DB_PATH="/var/www/daylog/daylog.db"
UPLOADS_PATH="/var/www/daylog/uploads"
PASS_FILE="/etc/daylog-backup.pass"
GDRIVE_DB_DEST="gdrive:daylog-backups"
GDRIVE_PHOTOS_DEST="gdrive:daylog-photos"
KEEP_BACKUPS=14
LOG="/var/log/daylog-backup.log"

TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
BACKUP_NAME="daylog-backup-${TIMESTAMP}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# == Preflight ==
[[ -f "$PASS_FILE" ]] || { log "ERROR: pass subor $PASS_FILE neexistuje"; exit 1; }
[[ -f "$DB_PATH"   ]] || { log "ERROR: databaza $DB_PATH neexistuje";     exit 1; }

# == Working dir ==
WORKDIR=$(mktemp -d)
trap 'log "Cleanup $WORKDIR"; rm -rf "$WORKDIR"' EXIT

log "=== Backup start: $BACKUP_NAME ==="

# ── CAST 1: DB — sifrovany archiv ────────────────────────────────────────────

log "[DB] Consistent SQLite snapshot..."
sqlite3 "$DB_PATH" ".backup ${WORKDIR}/daylog.db"

log "[DB] Balim archiv (len DB, bez uploads)..."
TAR_FILE="${WORKDIR}/${BACKUP_NAME}.tar.gz"
tar -czf "$TAR_FILE" -C "$WORKDIR" daylog.db
TAR_SIZE=$(du -sh "$TAR_FILE" | cut -f1)
log "[DB] Archiv OK: ${BACKUP_NAME}.tar.gz (${TAR_SIZE})"

log "[DB] Sifrujem GPG AES256..."
GPG_FILE="${WORKDIR}/${BACKUP_NAME}.tar.gz.gpg"
gpg --batch --yes \
    --passphrase-file "$PASS_FILE" \
    --symmetric \
    --cipher-algo AES256 \
    -o "$GPG_FILE" \
    "$TAR_FILE"
GPG_SIZE=$(du -sh "$GPG_FILE" | cut -f1)
log "[DB] GPG OK: $(basename "$GPG_FILE") (${GPG_SIZE})"

log "[DB] Uploadujem na ${GDRIVE_DB_DEST}/..."
rclone copy "$GPG_FILE" "${GDRIVE_DB_DEST}/" --log-level INFO 2>> "$LOG"
log "[DB] Upload OK"

log "[DB] Rotacia (ponechavam poslednych ${KEEP_BACKUPS})..."
BACKUP_LIST=$(rclone lsf "${GDRIVE_DB_DEST}/" --files-only \
    | grep "^daylog-backup-.*\.tar\.gz\.gpg$" \
    | sort)
TOTAL=$(echo "$BACKUP_LIST" | grep -c . || true)
if [[ "$TOTAL" -gt "$KEEP_BACKUPS" ]]; then
    DELETE_COUNT=$(( TOTAL - KEEP_BACKUPS ))
    TO_DELETE=$(echo "$BACKUP_LIST" | head -n "$DELETE_COUNT")
    log "[DB] Mazem ${DELETE_COUNT} starych zaloh..."
    while IFS= read -r OLD_FILE; do
        [[ -z "$OLD_FILE" ]] && continue
        rclone delete "${GDRIVE_DB_DEST}/${OLD_FILE}" 2>> "$LOG"
        log "[DB]   Zmazana: $OLD_FILE"
    done <<< "$TO_DELETE"
else
    log "[DB] Rotacia: ${TOTAL} zaloh, limit ${KEEP_BACKUPS} neprekroceny."
fi

# ── CAST 2: Fotky — prirastokovy copy (bez sifrovania) ───────────────────────
#
# Pouzivame rclone copy (nie sync), lebo:
#   - sync maze na cieli subory ktore uz na zdroji nie su
#   - ak fotka zmizne z VPS (chyba, zmazanie), sync by ju zmazal aj z Drivu
#   - copy len pridava nove/zmenene, nikdy nemaže — Drive je permanentny archiv
#

if [[ -d "$UPLOADS_PATH" ]]; then
    log "[Photos] Zacina rclone copy uploads -> ${GDRIVE_PHOTOS_DEST}..."
    PHOTOS_LOG=$(rclone copy "$UPLOADS_PATH/" "${GDRIVE_PHOTOS_DEST}/" \
        --log-level INFO \
        --stats-log-level INFO \
        --stats-one-line \
        2>&1 | tee -a "$LOG" || true)
    PHOTOS_COUNT=$(find "$UPLOADS_PATH" -type f | wc -l)
    log "[Photos] Copy OK: ${PHOTOS_COUNT} lokalnych suborov v uploads/"
else
    log "[Photos] uploads/ adresar neexistuje, preskakujem."
fi

log "=== Backup dokonceny: $BACKUP_NAME (DB: ${GPG_SIZE}) ==="
