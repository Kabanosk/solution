#!/usr/bin/env bash
set -euo pipefail
root="$(realpath "$(dirname "$0")/..")"
node "$root/scripts/prepare-assets.cjs"
RUSTUP_TOOLCHAIN=stable cargo build --manifest-path "$root/Cargo.toml" --release --target wasm32-unknown-unknown --features hydrate --lib
RUSTUP_TOOLCHAIN=stable wasm-bindgen "$root/target/wasm32-unknown-unknown/release/scout_dashboard.wasm" --target web --out-dir "$root/public/pkg" --out-name scout_dashboard
RUSTUP_TOOLCHAIN=stable cargo build --manifest-path "$root/Cargo.toml" --release --features ssr --bin scout-dashboard
