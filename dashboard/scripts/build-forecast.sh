#!/usr/bin/env bash

set -Eeuo pipefail

FORECAST_DASHBOARD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FORECAST_MANIFEST="${FORECAST_DASHBOARD_DIR}/forecast-plot/Cargo.toml"
FORECAST_LOCK="${FORECAST_DASHBOARD_DIR}/forecast-plot/Cargo.lock"
FORECAST_BINDGEN_BIN="${WASM_BINDGEN_BIN:-wasm-bindgen}"

if [[ ! -f "${FORECAST_MANIFEST}" || ! -f "${FORECAST_LOCK}" ]]; then
  printf 'Missing forecast crate or Cargo.lock: %s\n' "${FORECAST_MANIFEST}" >&2
  exit 1
fi

FORECAST_BINDGEN_VERSION="$(awk '
  /^\[\[package\]\]/ { wanted = 0 }
  /^name = "wasm-bindgen"$/ { wanted = 1 }
  wanted && /^version = / { gsub(/"/, "", $3); print $3; exit }
' "${FORECAST_LOCK}")"

if [[ -z "${FORECAST_BINDGEN_VERSION}" ]]; then
  printf 'Could not find the wasm-bindgen version in %s\n' "${FORECAST_LOCK}" >&2
  exit 1
fi

if [[ -z "${WASM_BINDGEN_BIN:-}" ]] && ! command -v "${FORECAST_BINDGEN_BIN}" >/dev/null 2>&1; then
  FORECAST_CACHED_BINDGEN="${XDG_CACHE_HOME:-${HOME}/.cache}/trunk/wasm-bindgen-${FORECAST_BINDGEN_VERSION}/wasm-bindgen"
  if [[ -x "${FORECAST_CACHED_BINDGEN}" ]]; then
    FORECAST_BINDGEN_BIN="${FORECAST_CACHED_BINDGEN}"
  fi
fi

if ! command -v "${FORECAST_BINDGEN_BIN}" >/dev/null 2>&1; then
  printf 'Install the matching CLI: cargo install wasm-bindgen-cli --version %s --locked\n' "${FORECAST_BINDGEN_VERSION}" >&2
  printf 'Or set WASM_BINDGEN_BIN to an existing matching wasm-bindgen executable.\n' >&2
  exit 1
fi

if [[ "$("${FORECAST_BINDGEN_BIN}" --version)" != "wasm-bindgen ${FORECAST_BINDGEN_VERSION}" ]]; then
  printf 'wasm-bindgen CLI must match Cargo.lock (%s).\n' "${FORECAST_BINDGEN_VERSION}" >&2
  printf 'Install: cargo install wasm-bindgen-cli --version %s --locked --force\n' "${FORECAST_BINDGEN_VERSION}" >&2
  exit 1
fi

cargo build --manifest-path "${FORECAST_MANIFEST}" --locked --release \
  --target wasm32-unknown-unknown --target-dir "${FORECAST_DASHBOARD_DIR}/target/forecast"

mkdir -p "${FORECAST_DASHBOARD_DIR}/assets/forecast"
"${FORECAST_BINDGEN_BIN}" \
  "${FORECAST_DASHBOARD_DIR}/target/forecast/wasm32-unknown-unknown/release/forecast_plot.wasm" \
  --target web --out-name forecast_plot --out-dir "${FORECAST_DASHBOARD_DIR}/assets/forecast"

printf 'Forecast built in %s/assets/forecast. Build or serve the dashboard with Trunk next.\n' "${FORECAST_DASHBOARD_DIR}"
