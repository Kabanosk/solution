# MLflow in this project

The tracking server and registry run in the cluster (`deploy/openshift/mlflow/`, in-cluster URI `http://mlflow:5000`, set
as `MLFLOW_TRACKING_URI` in ConfigMap `cfo-config`). UI: `oc get route mlflow -o jsonpath='{.spec.host}'` (no
authentication; prefer `oc port-forward svc/mlflow 5000:5000`). Three things use it:

| Experiment | Written by | What |
|---|---|---|
| `cfo-agent` | API and Scout worker (`cfo/observability.py`) | one trace per agent event, LLM calls as spans |
| `liquidity-forecast` | `cfo.forecast.model`, `tune --promote`, `train` | one run per trained forecast model, registered as model `liquidity-forecast` |
| `liquidity-forecast-tuning` | `python -m cfo.forecast.tune` | one parent run per search, one child run per trial |

The forecast code logs into its experiments by id and never calls `mlflow.set_experiment`, which would change the
process-wide active experiment: the API and the worker trace LLM calls in the same process and those traces would
silently move to `liquidity-forecast`.

## Forecast model lifecycle

```
nightly CronJob forecast-retrain ─┐                      ┌─> alias champion  ─> API (re-reads it every 60 s)
                                  ├─> new version ───────┤
Job forecast-tune (on demand) ────┘   of liquidity-forecast └─> alias challenger (every new version)
```

The API serves `models:/liquidity-forecast@champion` (`FORECAST_MODEL_URI` in `cfo-config`). While the registry is empty,
or MLflow is unreachable, it falls back to the model file at `FORECAST_MODEL_PATH` (trained on first use), so nothing
breaks before the first promotion.

### Start a search

```sh
oc create -f deploy/openshift/forecast/tune-job.yaml        # Job forecast-tune: 200 Optuna trials, --promote-if-better
oc logs -f job/forecast-tune                                 # progress and, at the end, the JSON summary
oc delete job forecast-tune                                  # to run it again (a Job spec is immutable) or to stop it
```

The Job runs `python -m cfo.forecast.tune --trials 200 --source db --promote-if-better --json` with the backend image,
1 CPU / 2Gi requested (limits 2 CPU / 4Gi), no GPU. 200 trials take a few minutes. `--source db` makes it fail instead of
tuning on the CSV when the database is unreachable; `backoffLimit: 0`, the finished pod is removed after a day (the runs stay in MLflow).
Do not use `oc apply -k deploy/openshift/forecast`: the kustomization only renders the two objects; applying it would also
start a search. Nothing in `deploy/` or the deploy workflow applies this directory.

### Watch it in the MLflow UI

Experiment `liquidity-forecast-tuning`, parent run `tuning-<date>`:

- **Model metrics** tab of the parent: `trial_balance_error_90d` and `best_so_far_balance_error_90d`, one point per
  trial (step = trial number), live while the search runs. Plus the summary metrics `best_*` / `baseline_*` / `top10_*`.
- Child runs `trial-NNN` (parameters, all rolling-origin scores, per-origin cumulative errors). Compare them in the table or
  with the parallel-coordinates chart. `python -m cfo.forecast.tune --log-curves` (off by default; one extra fit per trial)
  also logs each trial's per-round holdout curve.
- Parent artifacts: `best_params.json`, `top10_trials.csv`, `trials.csv`, `per_origin.csv`, `summary.json`,
  `optimization_history.png`. Read `per_origin.csv` before trusting the winner: the mean is dominated by one origin and
  a small history makes a lucky fit easy (see `.house/reports/T062.md`).

### How promotion works

One rule, `cfo.forecast.tune.promote`, used by both Jobs:

1. The candidate is retrained on the whole history and registered as a new version of `liquidity-forecast` with the alias
   `challenger`. `champion` is not touched by this step.
2. With `--promote-if-better` `champion` moves to the new version only if the candidate's real 90-day balance error beats
   the champion's by at least `--min-gain` (default 2 %). The champion is scored now, on the same data and folds (its
   feature set and parameters are retrained per fold), so an old champion is not flattered by older data. No champion
   yet: the candidate takes the alias. No real 90-day error on either side (history too short): `champion` stays.
3. When the registry cannot be asked (any error but "model / alias not found") nothing is registered, `champion` stays and
   the Job exits with code 2.

The log line says what happened, for example `champion v3 kept: candidate 90-day balance error 7,619 vs champion 7,412 (margin
not reached); registered v4 as challenger`. Each night adds one (small) version as `challenger`; delete old versions in the
UI when the list gets long. Note that the nightly Job trains the *default* configuration: a tuned champion is replaced
only when the default beats it by the margin, it is not refreshed with newer data by the retrain. Run `forecast-tune`
again after a few months of new bookings.

### Nightly retrain

```sh
oc apply -f deploy/openshift/forecast/retrain-cronjob.yaml                  # CronJob forecast-retrain, 01:30 UTC
oc create job forecast-retrain-manual --from=cronjob/forecast-retrain       # run it now
oc patch cronjob forecast-retrain -p '{"spec":{"suspend":true}}'            # pause (false = resume)
oc delete cronjob forecast-retrain                                          # remove
```

It runs `python -m cfo.forecast.train --source db --register --promote-if-better --json`, `concurrencyPolicy: Forbid`,
250m CPU / 1Gi requested. Locally: `uv run --project backend --extra mlflow python -m cfo.forecast.train --source csv`
trains and logs one run; `--register` registers it as `challenger`, `--promote-if-better` (implies `--register`) as above.

### Roll back

`champion` is an alias: point it at the previous version. In the UI: Models, `liquidity-forecast`, the version, Aliases.
From the command line (the API pod already has `MLFLOW_TRACKING_URI`):

```sh
oc exec deploy/api -- python -c "import mlflow; c = mlflow.MlflowClient(); print(c.get_model_version_by_alias('liquidity-forecast', 'champion').version)"
oc exec deploy/api -- python -c "import mlflow; mlflow.MlflowClient().set_registered_model_alias('liquidity-forecast', 'champion', '3')"
```

The API picks the old version up within a minute. To stop following the registry altogether, remove `FORECAST_MODEL_URI`
from `deploy/openshift/app/configmap.yaml` (or set it empty), `oc apply -k deploy/openshift/app` and
`oc rollout restart deploy/api deploy/scout-worker`: the model file takes over again.

### Training curves of the model runs

Every run in `liquidity-forecast` has stepped metrics, one point per boosting round, so the whole fit is a curve in the UI:
`train_rmse_curve`, `train_mae_curve` (the final model on its training days), `holdout_train_rmse_curve`,
`holdout_train_mae_curve` (the backtest fit on its training part) and `holdout_rmse_curve`, `holdout_mae_curve` (the same
fit on the last 60 days, which it did not see: where more trees stop helping). The values are in the units the booster models
(the target minus the baseline for the `level_baseline` feature set). The scalar metrics (`holdout_mae`, `holdout_rmse`,
`train_mae`, ...) are unchanged. The curves are recorded with an XGBoost `eval_set`, which does not change the fit (no early
stopping), and kept in memory only; they are not part of the saved model.

## Images

The Jobs run the `cfo-backend` image, which must contain the `mlflow` and `tuning` extras (`Containerfile` and
`Containerfile.dependencies` sync both). After a change of `backend/pyproject.toml`, `backend/uv.lock` or
`Containerfile.dependencies` rebuild the dependency image first, then the backend image:
`oc start-build cfo-backend-dependencies --follow --wait && oc start-build cfo-backend --follow --wait`
(the push-to-main workflow does that when those files changed).
