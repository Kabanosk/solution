# Native job queue verification

The `jobs` crate ports `backend/scout/jobs.py` into asynchronous Rust using the same PostgreSQL schema and SQL transaction boundaries. It captures a verified `Tenant` at construction and opens scoped connections through `scout-platform`.

Verified with rust-anything application record/replay: **8/8** CLI scenarios match the unchanged Python queue. Six explicit multi-operation scenarios cover empty queues, duplicate insertion, Unicode payloads and error truncation, stale attempt fencing, final-attempt reaping, retries, and the existing unfenced compatibility methods. Two generated traps characterize the driver's input boundary. Each run provisions and drops its own randomly named tenant schema in the explicitly configured local test database.

A real PostgreSQL concurrency integration test passes: sixteen racing duplicate inserts produce one row, sixteen concurrent workers claim sixteen distinct jobs, stale attempts cannot renew/complete/fail a replacement's lease, reaping increments the fencing token, and the third failed attempt is terminal. An added check verifies the actual scheduled retry delays (5, 30, 120 seconds), rather than only final status. `cargo fmt` and clippy with `-D warnings` pass.

Proof commands (set `SCOUT_RUST_TEST_DATABASE_URL` to the isolated local database):

```sh
cargo +stable clippy --manifest-path rust/jobs/Cargo.toml --all-targets -- -D warnings
cargo +stable test --manifest-path rust/jobs/Cargo.toml -- --ignored
rust-anything app replay rust/jobs-reference --build rust/jobs/target/debug/characterize --json
```

This is a plain Rust library characterized at the application boundary; it does not have a PyO3/NAPI unit `gate`. The source worker, watchers and loop handlers have not been replaced by this queue library alone. The queue's original global `scout_jobs` notification channel is preserved; this port does not silently change that behavior to the separate schema-qualified notifier used by API mutations.
