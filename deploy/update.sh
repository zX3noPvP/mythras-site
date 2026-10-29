#!/usr/bin/env bash
# Update the website from an archive WITHOUT touching secrets, database or venv.
# Usage (on the VPS, as root):
#   bash <(tar -xOzf /tmp/mythras-site.tgz mythras-anticheat-site/deploy/update.sh)
# or, once installed:   bash /opt/mythras-site/deploy/update.sh [/path/to/archive.tgz]
set -euo pipefail

ARCHIVE="${1:-/tmp/mythras-site.tgz}"
SITE=/opt/mythras-site

[ -f "$ARCHIVE" ] || { echo "Archive not found: $ARCHIVE"; exit 1; }

# never accept an archive that carries secrets, the database or a venv
if tar -tzf "$ARCHIVE" | grep -qE '(^|/)\.env$|/data/|/\.venv/'; then
    echo "Refusing: the archive contains .env, data/ or .venv/"; exit 1
fi

# safety copy of the database (users, orders, admin 2FA) in its own root-only folder; the newest 10 are kept
BACKUPS=/var/backups/mythras-site
KEEP=10
install -d -m 700 -o root -g root "$BACKUPS"
# copies made by older versions of this script, straight into /root
for old in /root/mythras.db.before-update-*; do
    [ -f "$old" ] || continue
    gzip -c "$old" > "$BACKUPS/$(basename "$old" | sed 's/^mythras\.db\.before-update-/mythras-/').db.gz" && rm -f -- "$old"
done
if [ -f "$SITE/data/mythras.db" ]; then
    snapshot="$BACKUPS/mythras-$(date +%F-%H%M%S).db"
    # SQLite online backup: consistent even while the site is writing
    "$SITE/.venv/bin/python" -c 'import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
        "$SITE/data/mythras.db" "$snapshot"
    gzip "$snapshot"
    chmod 600 "$snapshot.gz"
fi
{ ls -1t "$BACKUPS"/mythras-*.db.gz 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --

tar -xzf "$ARCHIVE" -C "$SITE" --strip-components=1
"$SITE/.venv/bin/pip" install -q -r "$SITE/requirements.txt"
"$SITE/.venv/bin/pip" install -q -r "$SITE/ticketbot/requirements.txt"

# permissions: code read-only for the app, only data/ writable, Caddy reads static/
chown -R root:mythras "$SITE"
chmod -R u=rwX,g=rX,o= "$SITE"
chown -R mythras:mythras "$SITE/data"
chmod 750 "$SITE/data"
chmod o+x "$SITE"
chmod -R o+rX "$SITE/static"

# health check (Discord alert) every 2 minutes + full backup every night: installed/refreshed on every update
for unit in "$SITE"/deploy/systemd/*.service "$SITE"/deploy/systemd/*.timer; do
    install -m 644 -o root -g root "$unit" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now mythras-healthcheck.timer mythras-backup.timer >/dev/null 2>&1 || echo "Warning: could not enable the timers"

systemctl restart mythras-site
# ticket bot: only once it has its token
if grep -qE '^TICKETS_BOT_TOKEN=.+' /etc/mythras-site.env 2>/dev/null; then
    systemctl enable mythras-tickets >/dev/null 2>&1 || true
    systemctl restart mythras-tickets || echo "Warning: ticket bot not started. Check: journalctl -u mythras-tickets -n 50 --no-pager"
fi
sleep 2
if systemctl is-active --quiet mythras-site && curl -fsS -o /dev/null http://127.0.0.1:8000/; then
    echo "Update OK"
else
    echo "Site not healthy. Check: journalctl -u mythras-site -n 50 --no-pager"
    exit 1
fi
