# Fix list from the Codex review of all merged work (2026-11-20)

Source: 19 Codex reviews (`house review` machinery, one per merged task + teammate PR #2), reports in
`.house/reports/review-<id>.md`. Base state: `house check --all` on main = 18/18 GREEN, so every item below is
something the frozen checks do not cover. Tag `[V]` = coordinator re-verified at HEAD; the rest are Codex findings,
static review only — confirm before fixing, drop if wrong (log the disagreement in the task report).

## P0 — the cluster demo cannot run (most belong in T011 / T023)

1. `[V]` **`python -m cfo.run` does not exist** (`deploy/cfo-entrypoint.sh:39`, `deploy/openshift/app/worker.yaml:20`).
   Worker pod crash-loops. This is T011's job; T011 is the critical path, nothing end-to-end works on the cluster
   without it. (review-T021, T023, T008, T005)
2. `[V]` **REST approvals always 503 on the cluster** (`backend/cfo/api/deps.py:54`). `set_agent()` expects the agent
   in the API process, but API and worker are separate pods. T011 must choose: approvals through Postgres (API marks
   the approval, worker picks it up and resumes the graph) — recommended — or run API inside the worker pod.
   (review-T020, T023)
3. `[V]` **Simulator routes 500 on the cluster**: `cfo/sim/control.py:19` reads `data/generated/events/december.jsonl`;
   the image has no such file and `api.yaml` does not mount PVC `cfo-data` (only MCP and seed job do). Mount
   `cfo-data` at `/app/data` in api + worker, or load events from Postgres. (review-T010, T020)
4. `[V]` **Slack adapter type mismatch** (`backend/cfo/channels/slack.py:178`): agent's `Notifier` passes an employee id
   string, adapter dereferences `employee.id` → `AttributeError` on the first DM once wired in T011. Tests pass
   `Employee` objects, so they miss it. Add a test with the real call shape. (review-T008)
5. **No production path from a CEO question to the liquidity review** (`backend/cfo/forecast/liquidity.py:467`): only
   tests submit the event. T011 must route the CEO "oven" what-if question → `forecast_with` → credit-line approval.
   (review-T016)
6. `[V]` **LLM config placeholders** (`deploy/openshift/app/configmap.yaml:12-15`, `CHANGE-ME`). Human/cluster value
   (T013/T029); without it no LLM call works.
7. **vLLM tool calling** (`backend/cfo/agent/policy.py:207`, and every tool-calling path): requests with `tools` are
   rejected unless vLLM runs with `--enable-auto-tool-choice --tool-call-parser llama3_json`. Verify the served
   model's args on the cluster; on an HTTP 400 for tools, fall back to the JSON-in-text path instead of failing.
   (review-T003)
8. `[V]` **Image has no `mlflow` extra** (`Containerfile:24,28`): `MLFLOW_TRACKING_URI` is set but logging is skipped
   on the cluster → T026/T027 tracing invisible. Add `--extra mlflow` to both `uv sync` calls. (review-T015)

## P1 — wrong results in the demo story

9. **Bank replay wipes reconciliation**: `add_transaction` upsert (`backend/cfo/db/repo.py:~104`, `cfo/data/load.py:76`)
   overwrites `documented`/`invoice_id` with defaults; relink is skipped because the invoice already has the txn id →
   payment shows as undocumented again. Upsert must not reset these columns. (review-T001, T002)
10. **`load --reset` keeps `sim_state`** (`cfo/data/load.py:27`, `deploy/openshift/db/seed-job.yaml:39`): after a reseed
    the simulator thinks December already replayed → empty demo. Reset the cursor too. (review-T002, T022)
11. **Attaching an existing invoice number to a second payment** overwrites that invoice and lines; first payment stays
    documented against it (`cfo/db/repo.py:~133`, `cfo/mcp_servers/company_db.py:146`); no row lock on the documented
    check (`company_db.py:137`). Refuse when the number is linked to another txn; `SELECT ... FOR UPDATE`. (review-T001, T004)
12. **Agent attaches by expected number, not extracted** (`cfo/agent/graph.py:223`): a document that merely mentions the
    expected number gets attached if the total matches. Compare the extracted invoice number. (review-T005)
13. **`EXECUTED` even when attach was refused** (`cfo/agent/graph.py:423`): check the attach result; on refusal record a
    failure and tell the CEO. (review-T005)
14. **CEO rule "anomaly alerts need approval" ignored** (`cfo/agent/graph.py:448`): level recorded as APPROVAL but the
    alert is sent immediately. Honour the level (interrupt) — rule override is part of the demo. (review-T005)
15. **Credit-line headline gives the wrong deadline** (`cfo/agent/graph.py:375`): uses the month of the deepest gap
    (June), but the proposal needs the line from March 1 in the €30k oven scenario. Use the proposal's start date. (review-T016)
16. **Cost alert without invoice lines** (`cfo/agent/investigate.py:37`): live path calls the detector without invoices,
    so the ad-spend alert has no contributing lines. Pass the invoices from Postgres. (review-T007)
17. **Small-model robustness** — Llama 3.2 3B will send odd shapes: scalar `lines` → `TypeError` outside the handler
    (`cfo/agent/llm_tasks.py:133`); tool args that are valid non-object JSON (`[]`, `null`) → Pydantic error aborts the
    turn (`cfo/llm.py:108`). Treat both as "no extraction". (review-T005, T001)
18. **Deploy guide skips the CSV upload** (`docs/cluster-setup.md:45`): fresh cluster → empty `sample-data` PVC → seed job
    404 → empty Postgres. Add the upload step (goes into T025). (review-PR2)

## P2 — cheap hardening that the bank jury may notice

19. **Postgres and the file server on 0.0.0.0** (`docker-compose.yml:9`, `:19`): bind to `127.0.0.1:` (one-line fix).
20. **Workstation symlink escape** (`cfo/mcp_servers/workstations.py:94`): check the resolved path lies inside an allowed
    company folder; D-004 ("only company folders") is part of the pitch. Also log `list_workstations` (`:114`) and
    reject NUL in paths before the audit insert (`:106`).
21. **Slack `app_mention` answers in the public channel** (`cfo/channels/slack.py:275`): CEO financial answers visible
    to the whole channel. Reply by DM / ephemeral.
22. **Approve as anyone via `by`** (`cfo/api/routers/approvals.py:47`) + unauthenticated Routes for API and MLflow
    (`deploy/openshift/app/api.yaml:55`, `deploy/openshift/mlflow/mlflow.yaml:63`). Minimum: a shared bearer token for
    write endpoints (approvals, sim) from the Secret; MLflow Route only if needed for the demo. Wildcard CORS stays
    until the dashboard URL is known.
23. **SSE blocks worker threads** (`cfo/api/stream.py:115`): sync generator holds a threadpool slot; several open
    dashboards can starve health probes. Make it an async generator. Initial frame without `id:` (`:111`) loses rows
    on early reconnect.
24. **Slack failure strands an approval** (`cfo/agent/graph.py:399`): retry or mark undelivered so it shows up again.
25. **Seed job not atomic** (`cfo/data/load.py`): `TRUNCATE` commits before the insert; do reset + load in one
    transaction. Job `activeDeadlineSeconds` includes the image build wait (`seed-job.yaml:13`).
26. **Forecast backtest leaks the holdout** (`cfo/forecast/features.py:230`, `cfo/forecast/model.py:193`): shutdown and
    due-day features are inferred from full history before the split → optimistic RMSE (and the API interval,
    `cfo/api/services.py:104`). These metrics land in MLflow in front of the jury: infer from the training window.
27. **`step` may advance several events** (`cfo/sim/engine.py:221`) — demo pacing; `--speed nan` accepted (`:190`).

## P3 — nice to have (skip unless idle)

- `policy.py:165` supplier median grouped by raw name (normalise first); `policy.py:141` "above 1%" text vs `>=`.
- December invoices seeded with issue dates after the sim clock (`cfo/data/generate.py:150`, intentional "invoice
  first", but dated in the future); December orders +3% instead of flat (`cfo/data/metrics.py:93`);
  `generate --out` rmtree's the whole dir (`generate.py:124`).
- Escalation looks at the last 500 decisions globally (`graph.py:273`); reminder to a recipient now on vacation (`:323`).
- Cost metrics: 0→€150 with zero orders skipped (`cost_metrics.py:204`); missing month treated as month-over-month (`:240`).
- API: 500 without CORS headers (`app.py:41`), 405 without `Allow` (`errors.py:97`), half-open date range not
  validated (`routers/costs.py:25`, `services.py:239`), `to=9999-12-31` → 500 (`services.py:225`), by-team ignores
  unlinked invoices (`services.py:241`).
- `load_daily_features()` crashes on empty `transactions` (`features.py:233`); `forecast_with` needs fixed-cost rows even
  with an explicit buffer (`liquidity.py:248`).
- Slack: HR CSV `slack_id` ignored (document `SLACK_USER_MAP` only), refusal messages without level label (`slack.py:209`).
- `db/init.sql:6` double import on rerun; `tests/test_repo.py:24` drops a fixed schema on the shared DB.
