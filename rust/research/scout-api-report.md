# Native Scout API data layer checkpoint

Implemented `rust/scout-api` (`scout-api` library), preserving the data operations in Scout's REST task/approval/audit/invoice/connections/strategy handlers. Public interface is ScoutApi::new(Database,Tenant), typed TaskListOptions/AuditOptions and ApiError { status, code, message }. Root application supplies Axum routes, query validation envelopes, request lifetime/authentication and SSE streaming.

Validated on 2026-11-21:

- **36/36 direct-handler characterization cases match** the unchanged original Python functions on the dedicated read-only HTTP fixture. Coverage: role-filtered lists/detail, multi-value/search/limit filters and counts, approval lists, audit filters, invoice detail and exact errors, connections and raw numeric/date/timestamp/BYTEA serialization. Corpus: scout-api/characterization.jsonl. Driver: scout-api-reference/characterize.py, native example characterize.rs. These read-only comparisons do not mutate fixtures.
- **1 isolated PostgreSQL integration test passed**, exercising all above data families plus approval approver/status checks, queued-only dedupe (including opposite second answer), strategy dedupe/role gate, document fallback, ciphertext omission and numeric fractional scale. It creates/drops a unique tenant schema; no production DB used.
- fmt and clippy --all-targets -- -D warnings pass.

Preserved specifics: list counts ignore filters but honor strategy visibility; only owner/ceo see strategy via REST; an approval answer enqueues a worker job without resolving the approval itself; the first deduped job wins; mutations use a transaction and per-schema pg_notify; clocks use Europe/Berlin strategy day; NUMERIC stays a string with scale, UTC datetimes use +00:00 and six fractional digits when present, BYTEA is omitted. Connections join only the credential-exists flag, not ciphertext.

No independent rust-anything unit gate is claimed for this plain Rust data library; root owns whole-HTTP recordings/replays. The library does not implement the task SSE transport, disabled legacy OAuth/demo routes, onboarding, chat, KSeF endpoints, forecast HTTP adapters or the worker loops. Legacy Slack scope retrieval retains the original five-second HTTP request and five-minute cache, but has not been exercised against the live Slack service (all fixture connections are tenant-local/nonlegacy).

Integration note: preserve every raw Authorization/organization header occurrence; use the validated Tenant from scout-platform throughout calls. Query bounds and FastAPI-compatible 422 details are the HTTP adapter's responsibility. Read row_json supports existing schema scalar types and NUMERIC widths, not arbitrary new PostgreSQL extension types.
