#!/usr/bin/env bash
# Nightly full backup (mythras-backup.timer), kept in /var/backups/mythras-site/nightly-*.tgz (newest 14).
# Contains: website database (with the ticket transcripts) and the transcript attachments, /etc/mythras-site.env, and the license server's database, settings and
# signing keys (keys/private.key: without it you cannot issue licenses any more). Root-only: it holds secrets.
# Copy it OFF the VPS too: tools/pull_backups.ps1 downloads the newest one to your PC.
set -euo pipefail

SITE=/opt/mythras-site
LICENSE=${MYTHRAS_LICENSE_DIR:-/opt/mythras-license}
OUT=/var/backups/mythras-site
KEEP=14
stamp=$(date +%F-%H%M%S)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
install -d -m 700 -o root -g root "$OUT"

snapshot() {  # SQLite online backup: consistent while the service keeps running
    "$SITE/.venv/bin/python" -c 'import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' "$1" "$2"
}

mkdir -p "$work/site" "$work/license"
[ -f "$SITE/data/mythras.db" ] && snapshot "$SITE/data/mythras.db" "$work/site/mythras.db"
[ -d "$SITE/data/transcripts" ] && cp -a "$SITE/data/transcripts" "$work/site/"  # ticket attachments
[ -f /etc/mythras-site.env ] && cp -a /etc/mythras-site.env "$work/site/"
if [ -d "$LICENSE" ]; then
    for db in "$LICENSE"/*.db; do
        [ -f "$db" ] && snapshot "$db" "$work/license/$(basename "$db")"
    done
    [ -f "$LICENSE/license.properties" ] && cp -a "$LICENSE/license.properties" "$work/license/"
    [ -d "$LICENSE/keys" ] && cp -a "$LICENSE/keys" "$work/license/"
fi

tar -czf "$OUT/nightly-$stamp.tgz.part" -C "$work" .
chmod 600 "$OUT/nightly-$stamp.tgz.part"
mv "$OUT/nightly-$stamp.tgz.part" "$OUT/nightly-$stamp.tgz"
{ ls -1t "$OUT"/nightly-*.tgz 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --
logger -t mythras-backup "saved $OUT/nightly-$stamp.tgz ($(du -h "$OUT/nightly-$stamp.tgz" | cut -f1))"
echo "$OUT/nightly-$stamp.tgz"
