# Native forecast model

Production Rust crate with no PyO3 dependency. `[lib].path` compiles the real shared implementation at `../forecast-model/src/port/native.rs`; it is the same code exercised by the rust-anything bridge and recorded corpus. See that crate's source-provenance.json, gate-report.json and THIRD-PARTY-NOTICES. Package Intel's BSD-3-Clause notice alongside binary distributions.

Use `ForecastModel::load(path)` or `from_bytes(model_json, metadata_json)`, then `with_levels_from`, `predict`, `features_for`, `band`, `contributions` and `explain`. Native libxgboost 3.4.1 must be packaged and available through `XGBOOST_LIB_PATH`; Python is not required.
