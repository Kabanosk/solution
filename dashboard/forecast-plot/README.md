# Lantern embedded charts

This standalone Rust `cdylib` renders both charts into one embedded WebGL canvas. The renderer uses **eframe =0.32.3**, with default features disabled and only `glow` and `default_fonts` enabled. `egui_plot =0.33.0` is compatible with egui 0.32 ([official release](https://github.com/emilk/egui_plot/releases/tag/0.33.0)). WebGL/Glow is the only compiled renderer; eframe 0.32.3 `WebOptions` has no renderer selector ([pinned source](https://github.com/emilk/egui/blob/0.32.3/crates/eframe/src/epi.rs)). No WebGPU dependency is enabled.

## Browser contract

The wasm-bindgen exports are `async start(canvas: HtmlCanvasElement): Promise<void>` and `stop(): void`. Initialization errors reject the promise. `stop()` destroys the runner and removes the canvas keyboard listener. The caller must serialize start/stop calls and call `stop()` before a canvas is removed during SPA navigation.

The parent reserves **500 CSS pixels** of height. Use a canvas with CSS `display:block; width:100%; height:500px` rather than relying only on HTML width/height attributes; eframe adjusts the physical drawing buffer for the device pixel ratio. Its built-in ResizeObserver handles size changes. Painting is event driven; this application requests no continuous animation. The canvas gets `tabindex=0`; supply an accessible label and an HTML summary/fallback alongside it because egui pixels do not provide ordinary DOM text to screen readers.

## Charts and controls

The top egui tabs select **Cash balance forecast** or **Expense mix**. Click the canvas or focus it with the keyboard first:

- `1` selects the forecast; `2` selects expenses.
- Forecast: `Ctrl +` / `Ctrl =` zooms in, `Ctrl -` zooms out, `Ctrl 0` resets the data bounds. Matching egui buttons provide pointer controls. Pointer dragging pans, scrolling zooms, and hover reveals amount/date values.
- Expenses: `Y` selects the full year, `Q` selects Q4. Matching egui selectors provide pointer controls; hovering a slice reveals its category, amount and percentage. The legend always exposes all values. A narrow canvas stacks the pie and legend with scrolling.
- `Tab` and `Shift Tab` return to the surrounding HTML page. This deliberately bypasses egui's canvas-internal Tab handling; the direct shortcuts above make every chart action available from the keyboard.

Browser Ctrl +/- zoom is prevented **only on this canvas**. Egui's separate keyboard UI scaling is disabled, so each shortcut changes the chart's data bounds once. Outside the canvas, ordinary browser shortcuts remain available.

## Data semantics

The forecast uses **cash balance in EUR on horizontal X** and **date on vertical Y**, with later dates higher. Its 93 daily points cover **20 November 2026 through 20 February 2027**, inclusive. The green marker is a scenario seed of **€42,000.00**, explicitly chosen solely for this synthetic demo. Its position at the scenario's first day does not claim a verified November 2026 balance. The orange dashed path contains deterministic illustrative daily receipts, procurement, payroll and contribution events. It is **mock data, not an actual projection or reconstructed historical series**. Reset shows the whole scenario; zoom may intentionally crop it.

The expense pie contains **recorded 2025 bank-statement aggregates**, not forecast points. It imports the dashboard's existing pure Rust `src/data.rs` to preserve exactly the same categorization, totals and Year/Q4 semantics. Its slice palette is the existing green, orange, red and neutral palette.

For browser verification, the canvas publishes `data-chart-view`, `data-chart-period`, `data-expense-total` and `data-plot-x-min`/`data-plot-x-max` after actual rendering. These attributes are diagnostics, not additional data sources.

## Build and verify

```bash
cargo test --manifest-path dashboard/forecast-plot/Cargo.toml
cargo build --manifest-path dashboard/forecast-plot/Cargo.toml --target wasm32-unknown-unknown --release
```

Run the dashboard's `scripts/build-forecast.sh` to generate the JavaScript/WebAssembly asset pair with the matching wasm-bindgen CLI **0.2.130**. The output name is `forecast_plot`. This crate does not mount or modify the surrounding Yew interface.
