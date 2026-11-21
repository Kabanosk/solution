# Dashboard runtime migration ledger

This crate is in progress. A Rust component existing does not prove browser parity. The original UI remains explicitly available through `?runtime=original`; delegated responses carry `X-Scout-Rust-Fallback: dashboard`. The full application is not yet a pure Rust runtime.

| Feature | Implementation state | Remaining parity/validation |
|---|---|---|
| Authentication forms | Rust find, create, login, Google sign-up/start; exact source copy/fields/constraints/slug autofill; pending/errors | Browser screenshots, keyboard/error focus, native auth-store wiring; join and one-use login-link screens pending |
| Design assets | Existing CSS tokens/classes compiled at build time; original Inter/Mono/Scout/Google/Lucide assets | Light/dark/mobile screenshot comparison |
| Theme | Rust persisted theme toggle | Cross-tab synchronization and no-flash comparison pending |
| Shell/navigation | Rust sidebar, groups/storage, workspace menu/switch/logout, hash aliases, history, search, shortcuts, mobile nav/drawer, DOM IDs | Compare focus/ARIA/menu arrow keys, unseen task/loop indicators, counts, sidebar geometry and refresh behavior |
| Tasks | Rust grouping/filters/scope, priority/status/channel rows, task detail fetch and approvals | Evidence/hiring report/rendering, owner avatars, cancelled-group collapse, live task polling and full audit details still in progress |
| Scout chat | Rust fetch/NDJSON streaming, abort/retry/new conversation, tool steps, context compaction, files/downloads, provider config, research, followup polling/ack | Browser streaming/abort and original model-unit comparison; exact remaining copy/layout/context limits; no full parity claim |
| Markdown | Rust source-subset renderer with escaped HTML and safe link schemes | Unit tests plus differential source cases/browser output pending |
| Overview/charts | Rust forecast/what-if/range, monotone SVG, buffer gauge, spend/activity donuts, cash events/tasks | 138 original TS helper cases match; browser chart geometry/tooltips/keyboard and visual comparison pending |
| Transactions/invoices | Rust filter/counts/bookings, native invoice drawer, lines/document, loading/errors | Browser filter/invoice/Escape passed and screenshots inspected; invoice cache, mismatch notice and evidence labels pending |
| Staffing | Rust directory/search, summary, whole-team CSV export, expandable analysis | Browser search/CSV/analysis passed; source screenshot comparison and mobile/dark views pending |
| Connections/onboarding | Rust source cards, Google callback, Gmail/Slack permissions, KSeF steps/invoices; wizard delegated | Browser expanded details passed and screenshot inspected; wizard and live KSeF refresh parity pending |
| Audit/policies/guardrails | Rust audit/filter, five original policy cards, owner switch/error state | Browser DENY/BLOCK and switch-state passed; global OFF banner, shared switch polling/write fencing pending |
| Strategy settings | Delegated in original; Rust task list exists | Rust settings panel and full strategy task view pending |
| LLM usage | Rust range/cache/reload, metrics, UTC buckets, SVG charts, actions/live snapshot | Compiles; browser/zero-fill characterization and full Recharts tooltip/geometry parity pending |
| Demo | Delegated to original | Rust gated scenario panel pending |
| Legal/public routes | Original assets native; legal pages delegated | Native legal content/routes pending |
| Server/data layer | Rust Axum SSR and explicit HTTP proxy | Dashboard-data SQL and `/api/*` logic are fallback until native handlers are wired |

Build dependencies may use Node/PostCSS to compile the existing CSS and copy licensed static SVG assets. Application rendering and event/state logic in this crate are Rust, browser logic compiled to WASM; generated JS loads WASM. Retaining any original API or page fallback remains incomplete runtime migration.
