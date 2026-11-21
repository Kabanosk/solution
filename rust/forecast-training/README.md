# Forecast runtime training characterization

The bridge records original training table/split algorithms, XGBoost training, feature-set fallback, holdout metrics/curves and runtime measured bands. Production links the same implementation through `../forecast-training-native`, with no PyO3 or Python runtime. Matching native libxgboost 3.4.1 is loaded using `XGBOOST_LIB_PATH`.

389 saved cases include 178 structured cases over actual native fits, every feature set, default 326-tree component training, fallback thresholds, 20/60-day holdouts, caller category order/duplicates, 310/365-day histories, rolling folds and band growth/asymmetry. Outputs compare exact IEEE754 bits; rounded band metadata is not compared with a tolerance.

The initializer is an empty zero-tree learner serialized by official XGBoost 3.4.1, with the requested feature count and automatic intercept enabled. It contains no trained state or tenant data. This preserves source defaults that xgb's `Booster::new` would override by explicitly specifying base_score=0.5. Every fit uses only its supplied history.
