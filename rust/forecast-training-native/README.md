# Tenant-local forecast training

Plain production crate. `[lib].path` compiles the same implementation as the rust-anything bridge at `../forecast-training/src/port/native.rs`, including native rolling-origin band measurement. It has no PyO3/Python dependency.

`train(&[scout_forecast_kernel::Transaction], &TrainingOptions)` returns `TrainedModel { model, curves }`. Options default to the source behavior: automatic components → level_baseline → calendar fallback, 60-day chronological holdout, measured band enabled and partner_investment excluded. Explicit feature sets enforce the original 28-open-day minimum. Category metadata preserves caller order and duplicates. `model.save(path)` writes portable JSON plus its sidecar; `model.model_bytes()` supports artifact stores. Set `band=false` for Scout's tenant request path. Native libxgboost 3.4.1 is required through `XGBOOST_LIB_PATH`; no global fitted model/cache is consulted.

See `../forecast-training/gate-report.json`, saved source cases and `../research/forecast-training.md`. MLflow remote artifact/logging transport and application DB/task orchestration are separate units.
