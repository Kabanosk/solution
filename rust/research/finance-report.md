# Rust financial display models and invoice parser

Completed unit: all four exports of `dashboard/lib/finance-model.ts` and `parseTransactionInvoice` from `dashboard/lib/invoice-model.ts`, characterized through JSON adapters to unchanged source copies. The type-only `scout-model.ts` reference is included for source completeness; its runtime helpers are **not** ported by this unit.

Implementation: `rust/finance/src/port.rs`; reference provenance and hashes: `rust/finance-reference/source.json`. The crate builds both as a native Rust library and through the tool's WASM bridge. No application frontend source was changed. The pure model unit uses no unsafe code and can be shared by server and browser Rust.

## Evidence

`rust/finance/gate.json`: **all checks passed; 399/399 cases match**, including 319 recorded errors, with 169/169 executable reference lines reached. Per-export cases: display cents 76; decimal cents 66; period aggregation 61; quarter spend 100; invoice 96. `rust/finance-reference/inputs.jsonl` contains 144 explicit cases; the tool adds generated cases. Two Rust regression tests preserve cent-bound rejection, integer percentage truncation and the existing quarter-label behavior. Line coverage does not imply exhaustive inputs or branch coverage.

Reproduce from repository root, using `RUSTUP_TOOLCHAIN=stable`:

```sh
rust-anything record rust/finance --count 30 --inputs rust/finance-reference/inputs.jsonl --node-arg=--experimental-strip-types
rust-anything build rust/finance
rust-anything diff rust/finance --json
rust-anything gate rust/finance --json
rust-anything bench rust/finance --min-ms 50 --json
```

The WASM build uses `wasm32-unknown-unknown` and `wasm-bindgen-cli` 0.2.130. The source-generated manifest currently contains the local absolute reference root; relocate/re-bridge it when moving the checkout. Exact dependency versions are locked in `rust/finance/Cargo.lock`.

## Public Rust interface

- `decimal_cents(&str) -> Result<BigInt, String>` and `display_cents(&BigInt) -> Result<f64, String>` retain exact integer cents until final display conversion.
- `finance_period(&Value) -> Result<Value, String>` consumes the original typed row shape and returns the original field shape/order.
- `quarter_spend_json(Option<&str>, Option<&str>, &Value) -> Result<String, String>` and `transaction_invoice_json(&Value) -> Result<String, String>` return serialized JSON so UTF-16 slicing can preserve an escaped lone surrogate when the original slices through a valid supplementary character. The internal `Value` builders are private because Rust Unicode scalar strings cannot directly represent that sliced result.
- `check_*` functions are characterization adapters, including validation of the original declared financial row shapes and JSON input parsing. Raw `BigInt` results are represented as decimal strings for the bridge. Native model output can be encoded with the same ECMAScript-compatible number/key serializer used by these adapters.

## Preserved behavior

Money strings parse strict two-decimal API values to arbitrary-size integers; display conversion rejects values outside ±9,007,199,254,740,991 cents. The string form accepted by `displayCents` keeps JavaScript BigInt whitespace/sign/base-prefix parsing and errors. Aggregations retain row order, null/empty category distinctions, existing palette token strings, BigInt percentage truncation towards zero, JS `Number(count)` conversions, and non-finite display values serialized as null.

Quarter rows keep null-ID filtering, descending spending, stable tie order and en-US locale collation for names. The existing source computes its quarter label using `floor(month / 3) + 1`; the port therefore preserves March→Q2 and December→Q5 instead of silently fixing those values. Default dates, invalid numeric month strings and UTF-16 slices are recorded.

Invoice parsing preserves object/text/line error order, default currency/source, nullable fields, decimal quantity/unit-price conversion, truncation of 1–4 fraction digits to cents without rounding, optional evidence, Number coercion of line position, and a 4,000 UTF-16-unit document excerpt. A regression recording cuts an emoji exactly at that boundary and requires the same escaped surrogate. Integer-like object keys in document evidence are serialized in JavaScript enumeration order; oversized JSON evidence numbers match JS rounding/non-finite serialization.

Dependencies: [num-bigint](https://docs.rs/num-bigint/latest/num_bigint/) supplies arbitrary precision; [ICU4X collation](https://docs.rs/icu_collator/latest/icu_collator/) supplies language-aware stable tie comparison; [ryu-js](https://docs.rs/ryu-js/latest/ryu_js/) supplies ECMAScript number formatting. The reference runtime resolves its default locale to en-US (Node ICU 77.1, Unicode 16.0). Inputs with accents, case differences and equal names are recorded; equivalence for every possible Unicode collation sequence or a different deployment default locale is not established.

## Scope and limits

This is a model/parser port, not a migration of SQL queries, REST handlers, charts, pages or browser interactions. JSON adapters explicitly validate the original declared row types before calling the original; their error messages for out-of-type rows are adapter contract checks, not evidence that the original untyped misuse behaves the same. The Rust JSON boundary accepts Unicode scalar input strings; UTF-16 slices created from valid scalar input are preserved at output. As with the NAPI primitive-string boundary, raw JSON input containing already-unpaired surrogate strings needs a shared WTF-8 JSON boundary before claiming unrestricted JavaScript-string equivalence. No tolerance or known-difference file is used. Performance observations are stored in `rust/finance/bench.json`; they include the WASM bridge rather than measuring the final native application.
