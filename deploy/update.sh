#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

compose=(docker compose --env-file .env -f deploy/docker-compose.vps.yml)

# Keep enough RAM available for BuildKit on small VPS instances. Only this
# project is stopped; unrelated Compose projects keep running.
"${compose[@]}" stop web connector worker 2>/dev/null || true
"${compose[@]}" build web
"${compose[@]}" up -d
"${compose[@]}" ps
