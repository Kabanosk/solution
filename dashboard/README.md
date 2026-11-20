# LedgerLoop dashboard

An interactive autonomous CFO dashboard built in Rust with Yew and compiled to WebAssembly. The MVP includes:

- an overview with synthetic expense aggregation from the synthetic demo statement;
- interactive full-year and Q4 expense charts;
- a priority inbox with a working review action;
- a connected-system finance loop and IAM status;
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

## Data provenance

`data/demo-bank-statement.csv` is a generated, wholly synthetic fixture. Employee names, salaries, balances and transaction details are fictional; no private source documents are needed.

## Design system

The frontend follows the public snapshot of Antmicro's Aesir design system: its token naming, Inter and Geist Mono typography, 4 px spacing scale, restrained surfaces, radii, button behavior, cards, badges and accessible interaction states. The generated product-level design guidance is stored under `design-system/ledgerloop/`.

See `THIRD_PARTY_NOTICES.md` and the font license files under `assets/fonts/`.

