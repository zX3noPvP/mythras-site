#!/usr/bin/env bash
# Every 2 minutes (mythras-healthcheck.timer): is the website up, and does it reach the license server?
# On a change (down -> up, up -> down) it posts to the Discord webhook in MONITOR_DISCORD_WEBHOOK
# (/etc/mythras-site.env). It needs 2 failed checks in a row before alerting, so a restart does not ping you.
set -uo pipefail

ENV_FILE=/etc/mythras-site.env
STATE_DIR=/var/lib/mythras-monitor
URL=http://127.0.0.1:8000/healthz

WEBHOOK=$(grep -E '^MONITOR_DISCORD_WEBHOOK=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true)
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

# /healthz answers JSON with 200 (all fine) or 503 (something is down); no answer = the website is down
body=$(curl -sS --max-time 10 "$URL" 2>/dev/null || true)
problem=""
if [ -z "$body" ]; then
    problem="the website does not answer"
elif ! echo "$body" | grep -q '"ok": *true'; then
    echo "$body" | grep -q '"license_server": *false' && problem="the license server does not answer"
    echo "$body" | grep -q '"database": *false' && problem="${problem:+$problem, }the website database fails"
    [ -z "$problem" ] && problem="health check failed"
fi
if systemctl cat mythras-license.service >/dev/null 2>&1 && ! systemctl is-active --quiet mythras-license; then
    problem="${problem:+$problem, }mythras-license service is stopped"
fi

fails=$(cat "$STATE_DIR/fails" 2>/dev/null || echo 0)
state=$(cat "$STATE_DIR/state" 2>/dev/null || echo up)

notify() {
    [ -n "$WEBHOOK" ] || return 0
    payload=$(printf '{"content": "%s"}' "$1")
    curl -fsS --max-time 10 -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK" >/dev/null 2>&1 || true
}

if [ -n "$problem" ]; then
    fails=$((fails + 1))
    echo "$fails" > "$STATE_DIR/fails"
    logger -t mythras-healthcheck "DOWN ($fails): $problem"
    if [ "$fails" -ge 2 ] && [ "$state" = up ]; then
        echo down > "$STATE_DIR/state"
        notify ":red_circle: **Mythras is down** on $(hostname): $problem. Check: journalctl -u mythras-site -u mythras-license -n 50"
    fi
else
    echo 0 > "$STATE_DIR/fails"
    if [ "$state" = down ]; then
        echo up > "$STATE_DIR/state"
        logger -t mythras-healthcheck "UP again"
        notify ":green_circle: **Mythras is back up** on $(hostname)."
    fi
fi
exit 0
