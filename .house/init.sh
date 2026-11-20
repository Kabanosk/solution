#!/usr/bin/env bash
# Run before every acceptance check: start local Postgres when the compose file exists.
set -e
root="$(git rev-parse --show-toplevel)"
if [ -f "$root/docker-compose.yml" ]; then
  docker compose -p cfo -f "$root/docker-compose.yml" up -d --wait db >/dev/null
fi
