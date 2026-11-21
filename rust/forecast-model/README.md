# Portable forecast model

Characterization bridge for existing XGBoost JSON artifacts and exact metadata sidecars. The deployed application links `../forecast-model-native`, which compiles the same native.rs source without PyO3. `../forecast-kernel` similarly shares the previously gated feature implementation. Python is used only for reference recordings and synthetic development fixtures.

`ForecastModel` loads model bytes/metadata, refreshes recent levels and component schedules, predicts, calculates asymmetric bands, native SHAP contributions and explanations. Free functions implement tranche scheduling and liquidity curves. Model feature order, f32/f64 transitions, accumulated balance order and output timestamps are preserved. Runtime training, MLflow resolution and liquidity recommendations remain separate open work.

The `xgb` safe wrapper dynamically loads libxgboost 3.4.1; set `XGBOOST_LIB_PATH` to the packaged shared library. Development comparison uses the matching library shipped in the original Python wheel. The Rust deployed program never imports Python or launches a Python process.

346 saved source cases compare IEEE754 bits exactly. A separate 1,024-case NumPy argsort corpus checks scalar tie ordering. Intel BSD-3-Clause attribution is retained in THIRD-PARTY-NOTICES; binary packaging must include it.
