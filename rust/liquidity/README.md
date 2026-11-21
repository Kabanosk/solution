# Liquidity characterization

Native safety buffers, gaps, surplus detection, what-if payments, SHAP narratives and policy-classified credit/investment proposals. Production uses the identical shared source through `../liquidity-native`.

352 saved original cases include 141 structured scenarios, all feature sets, 7/90/365-day reviews, exact buffer boundaries, recovery and non-recovery, multiple/sorted gaps, outflow mapping/tuple forms, duplicate inflow/outflow days, horizon rejection, CEO rule overrides, fractional reserves and negative-zero/rounding/scale. No original source was modified. `liquidity-reference/contract` import guards validate the original and snapshot hashes; results compare exact float bits and strings.

No Python is launched in deployed review/model/policy code. Runtime caller owns verified tenant DB access and explicit model selection. The model dependency loads matching native libxgboost 3.4.1. Read `../research/liquidity.md` and gate-report.json for precise scope.
