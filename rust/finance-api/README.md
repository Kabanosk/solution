# Financial read API

`FinanceApi::new(Database,Tenant,FinanceOptions)` exposes async `kpis()`, `forecast(usize,&str)`, `costs_by_team(Option<NaiveDate>,Option<NaiveDate>)`, and `cost_metrics(Option<&str>)`. Results are complete HTTP JSON response bodies; failures use `scout_api::ApiError`. Query parsing and validation details belong to the application HTTP adapter.

Options are explicit: `model_path` is the original base artifact filename, `safety_buffer_eur` is an optional decimal string, and `today` optionally pins empty-data range defaults. Every model path is namespaced as base.parent/tenants/verified_tenant.id/base.name. Models load/cache by mtime and train/save natively on first use; recent levels refresh per request. CPU model work runs off the async executor. Shared MLflow aliases are bypassed for a bound tenant, as in the original.

Evidence: 30 exact original-handler scenarios against a dedicated rich database fixture (including JSON field order); 295 original cost-metric cases; a real isolated DB test covering no-data responses, range validation, tenant artifact cold-start, portable saves and concurrent cached requests. This service reuses the gated feature/model/training implementations. Its own proof is direct source characterization; the root application owns authenticated HTTP replay.

`finance-api-reference/characterize.py --setup` writes only its dedicated local `scout_migration` fixture/schema and `/tmp/scout-finance-api-fixture`; imports validate source hashes. The Rust `characterize` example reads it and compares saved original responses. Matching native libxgboost 3.4.1 must be available via `XGBOOST_LIB_PATH`. Database tests additionally require the dedicated `SCOUT_TEST_DATABASE_URL` and `--ignored`.
