# Scout platform

Native PostgreSQL/session/tenant library. Application handlers receive a validated immutable `Tenant`; no public organization is inferred. `Database::scoped(&tenant)` establishes a fresh TLS-capable PostgreSQL connection with that tenant's startup `search_path`. `unscoped()` is reserved for the global authentication registry. URLs and credentials are omitted from `Debug`.

Public integration:

- `Tenant::new(id, slug, schema_name, employee_id, role, legacy)` validates registration. Field getters expose immutable values.
- `Database::new(url)`, `.scoped(&tenant)`, `.unscoped()` and `.explicit_schema(schema)`.
- `verify_session(&db, token, organization_slug)` returns the DB-verified membership, or `None` for invalid/expired/cross-workspace credentials.
- `authenticate_request(&db, method, path, &headers)` returns `None` only for GET/HEAD `/healthz`; otherwise returns a verified tenant or `AuthError { status, code, message }`. Preserve every raw header occurrence when constructing `headers` so duplicate rejection remains effective.
- Retain the returned tenant for the complete streamed response and pass it explicitly to every repository/job. `authenticate_websocket` has no health bypass.
- `load_worker_tenant(&db, organization_id)` requires explicit organization enrollment and an owner identity; `worker_tenant` reads `SCOUT_ORGANIZATION_ID`.
- `require_write_identity(tenant)` uses the authenticated employee identity, never an API token or caller-supplied actor.

Run `cargo +stable test --manifest-path rust/platform/Cargo.toml`. The 142-case JSON corpus is captured from the unchanged original by `backend/.venv/bin/python rust/platform-reference/characterize.py`.

Run the additional PostgreSQL integration test with `SCOUT_TEST_DATABASE_URL` explicitly set to an isolated disposable database, then `cargo +stable test --manifest-path rust/platform/Cargo.toml -- --ignored --test-threads=1`. It installs the existing auth schema, creates unique test organizations and schemas, verifies session expiry/header checks and parallel isolation, and removes only its own fixture rows/schemas.

The library uses system TLS trust through native-tls. Deployment must provide OpenSSL/native TLS dependencies and CA configuration. This is a platform unit; password login, OAuth, onboarding, API routes and workers remain separate migration work.
