# Lantern dashboard

An interactive autonomous CFO dashboard built in Rust with Yew and compiled to WebAssembly. The MVP includes:

- an overview with synthetic expense aggregation from the synthetic demo statement;
- interactive full-year and Q4 expense charts;
- a priority inbox linked to an interactive investigation;
- a bank → invoice registry → employee laptop → Slack evidence trail;
- a simulated Slack request, owner reply and invoice resolution;
- contextual sample CFO answers and a finance lead / intern IAM policy preview;
- a staffing and payroll view based on the synthetic scenario data.

## Run locally

Install the WebAssembly target and Trunk once:

```bash
rustup target add wasm32-unknown-unknown
cargo install trunk
```

Then run:

```bash
./dashboard/scripts/dev.sh --open
```

Open `http://127.0.0.1:8080` if the browser does not open automatically.
Trunk watches the Rust, HTML and CSS sources and reloads the browser after every successful rebuild.

## Verification

```bash
cargo test --manifest-path dashboard/Cargo.toml
trunk build dashboard/index.html --release
```

## Rust chart viewer build

The interactive chart viewer is a separate Rust `cdylib` in `forecast-plot/`, rendered with eframe, egui_plot and WebGL. Its egui tabs switch between **Cash balance forecast** and **Expense mix** on one canvas. The expense pie and its Year/Q4 selection use the synthetic demo aggregates from the shared `src/data.rs`; the pie is drawn in Rust. The development script builds the viewer's browser bindings before starting the Yew dashboard:

```bash
cargo install wasm-bindgen-cli --version 0.2.130 --locked
./dashboard/scripts/dev.sh --open
```

The CLI version must match `wasm-bindgen` in `forecast-plot/Cargo.lock`; the script checks this before building. Installing the CLI is optional if a matching Trunk-cached executable is already available under `${XDG_CACHE_HOME:-$HOME/.cache}/trunk/wasm-bindgen-VERSION/`. To reuse another installed executable outside `PATH`, set `WASM_BINDGEN_BIN` to its absolute path. Build output goes to `dashboard/target/forecast/` and browser assets to `dashboard/assets/forecast/`. Trunk's existing assets copy includes the generated JavaScript and WebAssembly. After editing the chart crate, restart the development script or run `bash dashboard/scripts/build-forecast.sh`; Trunk's live watcher alone rebuilds only Yew. For a production build, run that chart build helper before `trunk build dashboard/index.html --release`.

`assets/forecast-loader.js` mounts `lantern-cash-forecast-canvas` when Yew inserts it and stops its WebRunner when the canvas is removed. Loading or WebGL failures appear in `lantern-forecast-status`. The loader also exposes `window.LanternForecast.mount(canvasId)` and `.unmount(canvasId)` for explicit integration.

Cash forecast amounts and projected scenarios are illustrative fixtures, separate from the synthetic expense aggregates. They do not use a connected bank account or a financial forecasting service. The pie's category values and percentages are calculated from the synthetic demo statement.

## Data provenance

`data/demo-bank-statement.csv` is a generated, wholly synthetic fixture. Employee names, salaries, balances and transaction details are fictional; no private source documents are needed.

## Interactive design demo

Open **Investigations** or choose **Investigate** in the priority inbox. Inspect each evidence source, select **Send demo request**, then **Simulate Taylor’s reply**, and **Match invoice & resolve**. The overview updates from three open items to two. Investigation progress is retained during navigation for this browser session and resets on reload.

The investigation, Slack messages, invoice identifier, device paths and IAM policies are illustrative fixtures. No bank API, VPN device, Slack account or IAM service is connected. The role switch previews policy outcomes; it does not authenticate users or enforce server permissions. CFO questions return predefined sample answers. Only financial aggregates are calculated from the synthetic demo statement.

## Design system

The frontend follows the public snapshot of Antmicro's Aesir design system: its token naming, Inter and Geist Mono typography, 4 px spacing scale, restrained surfaces, radii, button behavior, cards, badges and accessible interaction states. The generated product-level design guidance is stored under `design-system/lantern/`.

Following `AGENTS.md`, use existing antmicro-aesir components only, dark tones with restrained green, red and orange accents, sparse icons, and a unique HTML `id` on every rendered element.

See `THIRD_PARTY_NOTICES.md` and the font license files under `assets/fonts/`.

