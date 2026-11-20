#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
DASHBOARD_DIR="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
for required_command in node npm; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${required_command}" >&2
    printf 'See %s/README.md for setup instructions.\n' "${DASHBOARD_DIR}" >&2
    exit 1
  fi
done

node "${DASHBOARD_DIR}/scripts/prepare-assets.mjs"

printf 'Starting Lantern with automatic reload...\n'
printf 'Local URL: http://127.0.0.1:3000\n'
printf 'Press Ctrl+C to stop.\n\n'

exec npm --prefix "${DASHBOARD_DIR}" run dev -- "$@"
