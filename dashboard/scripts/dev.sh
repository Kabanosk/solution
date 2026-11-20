#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
DASHBOARD_DIR="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
WASM_TARGET="wasm32-unknown-unknown"

for required_command in cargo rustup trunk; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${required_command}" >&2
    printf 'See %s/README.md for setup instructions.\n' "${DASHBOARD_DIR}" >&2
    exit 1
  fi
done

if ! rustup target list --installed | grep -qx "${WASM_TARGET}"; then
  printf 'Missing Rust target: %s\n' "${WASM_TARGET}" >&2
  printf 'Install it with: rustup target add %s\n' "${WASM_TARGET}" >&2
  exit 1
fi

# Some environments export NO_COLOR=1, while Trunk 0.21 expects a boolean.
# Removing it avoids a CLI parse error without changing the user's shell.
unset NO_COLOR

printf 'Starting LedgerLoop with automatic reload...\n'
printf 'Local URL: http://127.0.0.1:8080\n'
printf 'Press Ctrl+C to stop.\n\n'

cd "${DASHBOARD_DIR}"
exec trunk serve index.html "$@"

