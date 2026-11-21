# Native liquidity review

Production crate compiles the exact real implementation `../liquidity/src/port/native.rs` exercised by the rust-anything bridge. No PyO3 dependency.

Public operations: `fixed_costs_monthly`, `normalize_outflows`, `forecast_with`, `detect_gaps`, `find_surplus`, `propose_credit_line`, `propose_investment`, `proposal_payload` and `review_liquidity`. A Review exposes the forecast, exact money strings, gaps and policy-classified proposals; `.json()` is the characterization representation. Every model is supplied explicitly by tenant-scoped runtime orchestration; model retrieval/training belongs to the caller.

352 original cases match exact float bits, money strings and narratives; all 12 gate checks pass in `../liquidity/gate-report.json`. Matching native libxgboost 3.4.1 is needed for model-backed scenarios. Intel sorting notice remains in the model dependency and must ship with binaries.
