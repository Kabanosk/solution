# Scout Rust dashboard

Leptos renders the application on Axum and hydrates it from Rust/WASM in the browser. The original semantic CSS tokens, fonts, Scout/Google artwork and Lucide SVG paths are reused. See [FEATURES.md](FEATURES.md) for implemented components, delegated features and parity checks; this migration is not complete.

Build from the repository checkout with the installed stable Rust toolchain, `wasm32-unknown-unknown`, matching `wasm-bindgen-cli`, and the existing dashboard build dependencies:

```sh
./rust/dashboard/scripts/build.sh
```

Run the Rust server with `SCOUT_DASHBOARD_BIND` (default `127.0.0.1:3000`), `SCOUT_APP_ORIGIN` set to the public front-door origin, `SCOUT_DASHBOARD_ASSETS` pointing at the packaged `public` directory, and `SCOUT_DASHBOARD_UPSTREAM` pointing at the original dashboard (default `http://127.0.0.1:3001`). Configure the same public app origin on the upstream so its CSRF checks remain effective. The original service is explicit fallback while the remaining data handlers/views are ported; `?runtime=original` selects it for a page and proxied responses carry `X-Scout-Rust-Fallback: dashboard`.

Build-time Node/PostCSS compiles existing styles and copies licensed static assets. Production UI rendering, handlers and event logic here are Rust; only generated WASM loading/binding glue is JavaScript. The `public/pkg` artifacts must be included when packaging the server. Auth/finance source-language bridges are not linked into the server.

`tests/mock-upstream.mjs` is an isolated local browser-test fixture server using the original, clearly fictional UI fixtures. It never runs as part of the product. Browser smoke uses ports 3100/3101 and checks actual WASM hydration, auth mode/slug behavior, persisted theme, login errors, NDJSON Unicode streaming, task filters/detail, chat continuity, keyboard search, mobile overflow and unique DOM IDs. Screenshots are local ignored artifacts under `screenshots`.

```sh
node --experimental-strip-types rust/dashboard/tests/mock-upstream.mjs
# In another terminal, start the built Rust server on 127.0.0.1:3100 with upstream 127.0.0.1:3101.
node rust/dashboard/tests/browser-smoke.cjs
```

The fixture smoke does not establish database authentication, OAuth provider, Slack/bank/KSeF or production traffic parity. Do not remove application fallback based on this smoke alone.
