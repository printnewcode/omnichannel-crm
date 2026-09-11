#!/usr/bin/env bash
set -uo pipefail

project_dir="${OMNICHANNEL_PROJECT_DIR:-/home/omnichannel-crm}"
cd "$project_dir" || exit 1

exec 9>/run/lock/omnichannel-watchdog.lock
flock -n 9 || exit 0

compose=(docker compose --env-file .env -f deploy/docker-compose.vps.yml)

log() {
    logger -t omnichannel-watchdog "$*"
}

if ! systemctl is-active --quiet docker; then
    log "Docker is inactive; restarting it"
    systemctl restart docker || exit 1
    sleep 10
fi

# Restore stopped or missing services without touching healthy containers.
for service in db redis web connector worker; do
    container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null)"
    running=""
    if [[ -n "$container_id" ]]; then
        running="$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)"
    fi
    if [[ "$running" != "true" ]]; then
        log "$service is stopped; starting it"
        "${compose[@]}" up -d --no-build "$service" || true
    fi
done

database_id="$("${compose[@]}" ps -q db 2>/dev/null)"
database_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$database_id" 2>/dev/null || true)"

if [[ "$database_health" == "unhealthy" ]]; then
    log "MySQL is unhealthy; restarting database and application connections"
    "${compose[@]}" restart db || exit 1
    sleep 25
    "${compose[@]}" restart web connector worker || true
    exit 0
fi

domain="$(sed -n 's/^DOMAIN=//p' .env | tail -n 1 | tr -d '\r\"' | xargs)"
[[ -n "$domain" ]] || domain="localhost"

if ! curl --fail --silent --show-error --max-time 12 \
    -H "Host: $domain" \
    -H "X-Forwarded-Proto: https" \
    http://127.0.0.1:8001/api/health/ >/dev/null; then
    log "Web health check failed; restarting web"
    "${compose[@]}" restart web || true
fi
