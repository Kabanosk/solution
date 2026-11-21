# Native HTTP integration

The Rust/Axum migration entry point now implements the source session/organization middleware, health, task list/detail, approvals list/answer queue, audit list, invoice details, connections reads and strategy-review queueing. Remaining routes use explicit counted original-language fallback.

`rust-anything app replay`: **330/330** original HTTP cases pass, including per-request PostgreSQL data and sequence effects. One recorded case (an authenticated unknown endpoint) uses the source fallback. This corpus is primarily health/auth boundaries plus representative authenticated Scout reads/errors; its 0.3% fallback figure is **not** total application coverage. Chat/worker/forecast/onboarding/dashboard end-to-end flows still need recordings and native implementation.

A separate real-server differential test passes **31/31** byte/status/header comparisons: duplicate Authorization/organization headers, percent-encoded paths, Pydantic integer-query variants/invalid values, CORS and all health methods. Both source and Rust server deliver the initial task SSE event immediately. The generated proxy originally buffered complete bodies; it now forwards streaming bodies.

The original is never edited to make these comparisons pass. Native differences found and corrected include HEAD 405 handling, CORS Vary headers, raw non-ASCII request-target rejection and Axum's automatic Allow header on authorization errors. No recording was changed to waive those differences.

Test launchers copy original packages into a temporary tree, discard inherited credentials, and allow only an explicitly configured local fixture database. `http-fixtures.py` and `http-db-dump.py` refuse any database other than local `scout_migration`. They own a deterministic fixture organization/schema; queue/platform tests use independent schemas. Recorded fixtures contain artificial test data and a local-only test token.

Reproduce after building `rust/app` and selecting the isolated local database through `SCOUT_RUST_TEST_DATABASE_URL`:

```sh
python rust/scripts/http-fixtures.py
python rust/scripts/http-differential.py
rust-anything app replay . --build rust/app/target/debug/reference-api --json
```

Resolve Python through `backend/.venv/bin` on PATH for rust-anything (it canonicalizes explicit interpreter symlinks). The app remains a migration harness with original fallback. It has not passed lift or replaced production.
