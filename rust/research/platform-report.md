# Native platform checkpoint

Implemented `rust/platform` as the `scout-platform` plain Rust library. Public API contract: immutable validated Tenant with getters; Database::new/scoped/unscoped/explicit_schema; verify_session; authenticate_request/authenticate_websocket; load_worker_tenant/worker_tenant; require_write_identity. Uses tokio-postgres with JSON/chrono/UUID codecs and native-tls. No Python/Node is invoked by the library.

The 142-case pure characterization corpus (`platform/characterization.jsonl`) is captured from unchanged cfo.tenancy and cfo.api.auth by `platform-reference/characterize.py`. It covers UUID normalization, schema registration, explicit/current tenant scope, unsafe and cross-tenant schema access, legacy connector opt-in, membership write identity and token/slug grammar. Source hashes are pinned in platform/source-provenance.json.

Validation on 2026-11-21:

- Native replay of original pure corpus: **142/142 match**.
- Default Rust tests: **3 passed**, one explicitly isolated DB integration test excluded by default.
- Explicit DB integration test: **1 passed** against the local disposable migration PostgreSQL instance. It checks sessions, expiry, cross-organization rejection, enrollment, worker owner binding, missing/malformed/duplicate headers, GET/HEAD-only health bypass, websocket denial, DB-unavailable 503, blocked legacy/demo routes and concurrent independent tenant search_paths. Fixture rows and tenant schemas were removed afterwards; the original auth schema remains in the disposable database.
- cargo fmt and clippy --all-targets -- -D warnings pass.

Authorization mirrors current effective behavior: all protected routes including docs require a session; global mutation/demo URLs are denied even after authentication; request identity is carried explicitly through streamed responses and repositories instead of thread-local/global state. There is no implicit public tenant or global credential inheritance.

This library has not independently passed a rust-anything unit gate; its pure reference corpus is run by Rust tests. The root application owns whole-HTTP record/replay integration, including live authenticated request scenarios. TLS certificate failure/remote PostgreSQL behavior has not been integration-tested; only local PostgreSQL sslmode negotiation was exercised. No deployment/cutover or production DB mutation occurred.
