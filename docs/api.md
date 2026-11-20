# Autonomous CFO API

REST API behind the dashboard (Pizzeria Amalfi). Interactive docs: `http://localhost:8080/docs`. Machine-readable
contract: [`docs/openapi.json`](openapi.json), also served at `/openapi.json` (generate TypeScript types from it,
e.g. `npx openapi-typescript docs/openapi.json -o api.d.ts`).

**Status: all endpoints below return real data from the database** (`"stub": false`, no `X-Stub` header; the
`stub` field stays in every body for compatibility). The shapes are those of the former stubs, with two additive
fields for the safety buffer (see the forecast section); the example values below come from the seeded year after
the December replay (as of 2025-12-31).

## Run it

```bash
uv run --project backend uvicorn cfo.api.app:app --port 8080 --reload
```

The read endpoints need the seeded Postgres (`DATABASE_URL`, `python -m cfo.data.load --data data/generated --reset`); `/healthz` does not. Allowed browser origins: env `CORS_ORIGINS` (comma-separated, default `*`).
Regenerate the contract after API changes: `uv run --project backend python -m cfo.api.export_openapi docs/openapi.json`.

## Conventions

| Topic | Rule |
|---|---|
| Prefix | everything is under `/v1`, except `GET /healthz` |
| Naming | plural nouns, kebab-case paths (`/cost-metrics`), snake_case JSON fields |
| Money | object `{"amount": "1234.50", "currency": "EUR"}`; `amount` is a **string** with two decimals, negative = outflow or balance below zero. Never parse it as a float for arithmetic |
| Percent / ratios | string decimals: `"50.0"` means +50.0 %; `share` is `0..1` |
| Dates | ISO 8601 `2025-12-31`; months are the first day (`2025-12-01`); timestamps are UTC with `Z`: `2025-12-30T09:14:22Z` |
| Collections | `{"items": [...]}`. Lists that can grow are paginated with `limit` (1..200, default 50) and `offset` (default 0) and return `total`, `limit`, `offset` next to `items` |
| Enums | autonomy level `AUTO` / `NOTIFY` / `APPROVAL`; decision status `executed` / `pending` / `rejected` / `escalated`; cost pair `food_cost` / `ads_cac` / `cloud_per_order` |
| Errors | always `{"error": {"code", "message"}}` with the matching HTTP status (see below) |
| Auth | none for reads. Write endpoints (approve/reject, `/v1/sim:start`, `:pause`, `:step`) need `Authorization: Bearer <token>` when the server has `API_WRITE_TOKEN` set, see [Write protection](#write-protection) |
| Unknown fields | ignore them: new fields may be added without a version bump |

### Errors

```json
{
  "error": {
    "code": "not_found",
    "message": "Decision 'dec_nope' does not exist."
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 404 | `not_found` | unknown id, or unknown endpoint (`"No such endpoint."`) |
| 405 | `method_not_allowed` | wrong HTTP method; the `Allow` header lists the right ones |
| 401 | `unauthorized` | write endpoint without the right bearer token (only when `API_WRITE_TOKEN` is set); `WWW-Authenticate: Bearer` |
| 403 | `forbidden` | `by` (or the CEO, when a write token is used) is not the approver of the approval |
| 409 | `conflict` | the approval was already answered the other way |
| 422 | `validation_error` | bad query parameter; `error.details[]` lists `loc`, `message`, `type` |
| 500 | `internal_error` | bug on our side; no internal details are leaked. Carries the CORS headers like every other response, so the browser can read it |
| 503 | `database_unavailable` | the database cannot be reached |
| 503 | `no_data` | the database has no transactions yet (not seeded) |
| 503 | `agent_unavailable` | no agent runs in this API process (approvals cannot be answered) |

Validation error example (`GET /v1/decisions?limit=1000`):

```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid request: query.limit: Input should be less than or equal to 200.",
    "details": [
      {
        "loc": [
          "query",
          "limit"
        ],
        "message": "Input should be less than or equal to 200",
        "type": "less_than_equal"
      }
    ]
  }
}
```

### Write protection

By default the API is open (local development, mocks). When the server runs with the environment variable
`API_WRITE_TOKEN` set, the endpoints that change state require it:

* `POST /v1/approvals/{id}:approve`, `POST /v1/approvals/{id}:reject`
* `POST /v1/sim:start`, `POST /v1/sim:pause`, `POST /v1/sim:step`

```bash
curl -s -X POST http://localhost:8080/v1/sim:pause -H "Authorization: Bearer $API_WRITE_TOKEN"
```

Without the header, or with a wrong token or another scheme, the answer is `401 unauthorized` with
`WWW-Authenticate: Bearer` (checked before the body is validated). Reads, `/healthz` and `GET /v1/events` stay open.
With a token, **`by` in the approval body is ignored**: the token stands for the CEO (`CEO_ID`, default `vincenzo`),
so it can answer approvals addressed to the CEO and gets 403 for the others. Without a token `by` is still
the caller's claim. Slack buttons do not use the API (the agent receives them directly) and keep working. In the
cluster the token is the key `API_WRITE_TOKEN` of the Secret `slack` (the one the api Deployment imports), see `deploy/openshift/app/README.md`. The dashboard must keep the token out of
the page source if the page is public; a shared secret is a minimum, not a login.

## Autonomy levels in the UI

Each decision has a `level` (AUTO: did it alone, NOTIFY: did it and informed, APPROVAL: needed the CEO) and
`executed_simulated: true` when a real-world action (credit line, payment) was only simulated: show it as
**SIMULATED**.

---

## `GET /healthz`

Liveness probe, no database access.

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## `GET /v1/kpis`

Headline tiles: balance, average monthly cash flow, the lowest forecast balance of the next 12 months, paid
invoices without a document (Belegpflicht risk), pending approvals and open cost-vs-metric anomalies.

* `balance` is the running sum of all bookings (the statement has no opening balance), `as_of` the last booking day.
  `balance_change_30d` is the net of the bookings in the 30 days up to `as_of`.
* `avg_monthly_inflows` / `avg_monthly_outflows` average whole calendar months of bookings (a month still running at
  `as_of` is left out; all bank lines count, including the partner tranches).
* `forecast`: `lowest_balance`/`lowest_date` are the lowest point of the next 365 days (usually right after `as_of`,
  the June dip is a local minimum). `first_negative_date` is the first day below zero (null on the real data, D-013).
  **New:** `safety_buffer` and `first_below_buffer_date` (first day below the buffer, null if none).
* `pending_approvals` counts approval requests with status `pending`; `open_cost_alerts` counts pairs whose latest
  month is an anomaly.

```bash
curl -s http://localhost:8080/v1/kpis
```

```json
{
  "stub": false,
  "as_of": "2025-12-31",
  "balance": {
    "amount": "32945.51",
    "currency": "EUR"
  },
  "balance_change_30d": {
    "amount": "9582.66",
    "currency": "EUR"
  },
  "avg_monthly_inflows": {
    "amount": "45368.69",
    "currency": "EUR"
  },
  "avg_monthly_outflows": {
    "amount": "-42623.23",
    "currency": "EUR"
  },
  "forecast": {
    "horizon_days": 365,
    "lowest_balance": {
      "amount": "28905.14",
      "currency": "EUR"
    },
    "lowest_date": "2026-01-01",
    "first_negative_date": null,
    "safety_buffer": {
      "amount": "17730.00",
      "currency": "EUR"
    },
    "first_below_buffer_date": null
  },
  "undocumented_payments": {
    "count": 9,
    "total": {
      "amount": "7333.21",
      "currency": "EUR"
    }
  },
  "pending_approvals": 0,
  "open_cost_alerts": 1
}
```

## `GET /v1/forecast/liquidity`

Predicted end-of-day bank balance (XGBoost on calendar features, T015) with an 80 % prediction interval, the
**safety buffer line** and the stretches where the balance is predicted below it. `lowest_point` and `gaps`
always cover the whole horizon.

**Changed vs the stub (D-013):**
* New field `safety_buffer` (Money): draw it as a horizontal line. Default one month of fixed costs, the largest
  month of payroll + rent + tax + social security + utilities in the data (17,730.00 EUR on the seeded year);
  override with the env var `SAFETY_BUFFER_EUR`.
* `gaps` are now stretches **below `safety_buffer`**, not below zero. On the real data the balance stays above
  the buffer, so `gaps` is empty and the June 2026 shutdown shows as a dip of about 14k (the balance falls from about
  60k to about 46k). The credit-line story is a what-if scenario (later), not the base case.
* `opening_balance` is the balance at `as_of` (the last booking day); the forecast starts the day after.
* The interval is an approximation: 1.28 x the model's holdout RMSE x sqrt(days ahead), i.e. day errors are treated
  as independent. `model.version` is `trained-until-<date>`, `model.trained_at` the time the model file was saved.
* The model file (`models/liquidity_xgb.json`, or `FORECAST_MODEL_PATH`) is trained on the first request if missing
  (takes about a second) and reused afterwards.

| Query | Default | Meaning |
|---|---|---|
| `horizon_days` | `90` | 7..365. Use 90 for the dashboard chart, 365 to see the June 2026 dip |
| `granularity` | `week` | `day` or `week`; spacing of `points` (weekly points are the last day of each 7-day block; the lowest day is always included) |

```bash
curl -s "http://localhost:8080/v1/forecast/liquidity?horizon_days=365"
```

Response (points shortened):

```json
{
  "stub": false,
  "as_of": "2025-12-31",
  "horizon_days": 365,
  "granularity": "week",
  "currency": "EUR",
  "opening_balance": {
    "amount": "32945.51",
    "currency": "EUR"
  },
  "safety_buffer": {
    "amount": "17730.00",
    "currency": "EUR"
  },
  "lowest_point": {
    "date": "2026-01-01",
    "balance": {
      "amount": "28905.14",
      "currency": "EUR"
    },
    "lower": {
      "amount": "28513.80",
      "currency": "EUR"
    },
    "upper": {
      "amount": "29296.49",
      "currency": "EUR"
    }
  },
  "gaps": [],
  "model": {
    "name": "xgboost-liquidity",
    "version": "trained-until-2025-12-31",
    "mlflow_run_id": null,
    "trained_at": "2026-11-20T14:00:16.603852Z"
  },
  "points": [
    {
      "date": "2026-01-01",
      "balance": {
        "amount": "28905.14",
        "currency": "EUR"
      },
      "lower": {
        "amount": "28513.80",
        "currency": "EUR"
      },
      "upper": {
        "amount": "29296.49",
        "currency": "EUR"
      }
    },
    {
      "date": "2026-01-07",
      "balance": {
        "amount": "34365.81",
        "currency": "EUR"
      },
      "lower": {
        "amount": "33330.41",
        "currency": "EUR"
      },
      "upper": {
        "amount": "35401.20",
        "currency": "EUR"
      }
    },
    "..."
  ]
}
```

Each point: `{"date": "2026-01-07", "balance": {"amount": "41607.56", "currency": "EUR"}, "lower": {"amount": "41053.56", "currency": "EUR"}, "upper": {"amount": "42161.56", "currency": "EUR"}}`.

## `GET /v1/costs/by-team`

Costs per team over a range, with a monthly breakdown for stacked bars. Teams are sorted by total, highest first.

| Query | Default | Meaning |
|---|---|---|
| `from` | first day of the first booking month | first day (ISO date) |
| `to` | last booking day | last day (ISO date) |

The range is checked after the defaults: `from` after `to` (also when only one of them is given, e.g. `from` after the last booking) and a range longer than 120 months (`to=9999-12-31`) are a 422. Without any bank booking the range follows the unlinked invoices, which are costs too.

```bash
curl -s "http://localhost:8080/v1/costs/by-team?from=2025-10-01&to=2025-12-31"
```

Response (list shortened; the full list has the 5 teams kitchen, front, marketing, it, equipment, plus `company` for costs without a team: tax and social security, `owner_id` and `monthly_budget` null). Bank lines count in the booking month, invoices not yet linked to a payment in their issue month (ads, cloud and the oven are invoices only):

```json
{
  "stub": false,
  "from": "2025-10-01",
  "to": "2025-12-31",
  "total": {
    "amount": "127738.86",
    "currency": "EUR"
  },
  "items": [
    {
      "team_id": "kitchen",
      "team_name": "Kitchen / food",
      "owner_id": "rosa",
      "monthly_budget": {
        "amount": "28300.00",
        "currency": "EUR"
      },
      "total": {
        "amount": "83074.05",
        "currency": "EUR"
      },
      "avg_monthly": {
        "amount": "27691.35",
        "currency": "EUR"
      },
      "share": "0.6503",
      "by_month": [
        {
          "month": "2025-10-01",
          "cost": {
            "amount": "27714.66",
            "currency": "EUR"
          }
        },
        {
          "month": "2025-11-01",
          "cost": {
            "amount": "26994.94",
            "currency": "EUR"
          }
        },
        "..."
      ]
    },
    {
      "team_id": "front",
      "team_name": "Front of house and admin",
      "owner_id": "vincenzo",
      "monthly_budget": {
        "amount": "9500.00",
        "currency": "EUR"
      },
      "total": {
        "amount": "25800.00",
        "currency": "EUR"
      },
      "avg_monthly": {
        "amount": "8600.00",
        "currency": "EUR"
      },
      "share": "0.2020",
      "by_month": [
        {
          "month": "2025-10-01",
          "cost": {
            "amount": "8600.00",
            "currency": "EUR"
          }
        },
        {
          "month": "2025-11-01",
          "cost": {
            "amount": "8600.00",
            "currency": "EUR"
          }
        },
        "..."
      ]
    },
    "..."
  ]
}
```

## `GET /v1/cost-metrics`

The three cost-vs-metric pairs as monthly series: food cost vs orders, ads vs new customers (CAC), app cloud vs
online orders. `cost_per_unit` is the cost per order, new customer or online order (see `unit`); it is `null`
when the metric is zero (June 2025: shutdown). A point with `anomaly` set is a month where cost grew by 20 % or
more while the metric grew by less than 10 %, with the explanation for the alert card.

| Query | Default | Meaning |
|---|---|---|
| `kind` | all | `food_cost`, `ads_cac` or `cloud_per_order` |

```bash
curl -s "http://localhost:8080/v1/cost-metrics?kind=cloud_per_order"
```

Response (only the last two of 12 points shown):

```json
{
  "stub": false,
  "items": [
    {
      "kind": "cloud_per_order",
      "label": "App cloud vs online orders",
      "team_id": "it",
      "unit": "online_order",
      "points": [
        {
          "month": "2025-11-01",
          "cost": {
            "amount": "244.01",
            "currency": "EUR"
          },
          "metric_value": "278",
          "cost_per_unit": {
            "amount": "0.88",
            "currency": "EUR"
          },
          "cost_change_pct": "-2.2",
          "metric_change_pct": "-10.6",
          "anomaly": null
        },
        {
          "month": "2025-12-01",
          "cost": {
            "amount": "366.02",
            "currency": "EUR"
          },
          "metric_value": "286",
          "cost_per_unit": {
            "amount": "1.28",
            "currency": "EUR"
          },
          "cost_change_pct": "50.0",
          "metric_change_pct": "2.9",
          "anomaly": {
            "summary": "Cloud cost for the ordering app rose 50.0 % month over month while online orders grew only 2.9 %: log ingestion and retention went from EUR 26.95 to EUR 147.23, cost per online order from EUR 0.88 to EUR 1.28.",
            "drivers": [
              {
                "label": "Log ingestion and retention",
                "previous": {
                  "amount": "26.95",
                  "currency": "EUR"
                },
                "current": {
                  "amount": "147.23",
                  "currency": "EUR"
                }
              },
              {
                "label": "Compute (app backend)",
                "previous": {
                  "amount": "127.43",
                  "currency": "EUR"
                },
                "current": {
                  "amount": "128.44",
                  "currency": "EUR"
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

## `GET /v1/decisions`

History of everything the agent decided or did, newest first (steps 2 to 7 of the demo). Paginated. Read from the `decisions` table, which the agent fills while it works: the list is empty on a freshly seeded database. The example below is illustrative.

| Query | Default | Meaning |
|---|---|---|
| `limit`, `offset` | `50`, `0` | pagination |
| `level` | all | `AUTO`, `NOTIFY`, `APPROVAL` |
| `status` | all | `executed`, `pending`, `rejected`, `escalated` |
| `action_type` | all | e.g. `attach_invoice`, `escalate_missing_invoice`, `alert_cost_anomaly`, `request_credit_line`, `create_rule`, `pay_invoice` |

```bash
curl -s "http://localhost:8080/v1/decisions?level=APPROVAL&limit=2"
```

```json
{
  "stub": false,
  "items": [
    {
      "id": "dec_5b7e41c0a9d3",
      "ts": "2025-12-31T14:00:00Z",
      "action_type": "pay_invoice",
      "summary": "Pay invoice EQ-2026-0001 (EUR 1,280.00) from Gastro Tech Ltd for a dough mixer.",
      "level": "APPROVAL",
      "reason": "CEO rule: equipment always needs my approval.",
      "status": "pending",
      "actor": "agent",
      "payload": {
        "supplier": "Gastro Tech Ltd",
        "amount": "-1280.00",
        "currency": "EUR",
        "category": "equipment",
        "rule_id": "rule_3c9d7a1e5f20"
      },
      "executed_simulated": false,
      "approval_request_id": "apr_8e2f6b3a1c47"
    },
    {
      "id": "dec_c71d3f80e5a2",
      "ts": "2025-12-31T09:42:15Z",
      "action_type": "request_credit_line",
      "summary": "Credit line of EUR 25,000 approved by Vincenzo; application sent to the bank (simulated).",
      "level": "APPROVAL",
      "reason": "Credit lines always need CEO approval.",
      "status": "executed",
      "actor": "vincenzo",
      "payload": {
        "amount": "25000.00",
        "currency": "EUR",
        "lowest_forecast_balance": "-20430.00",
        "lowest_forecast_date": "2026-06-27",
        "forecast_model": "xgboost-liquidity"
      },
      "executed_simulated": true,
      "approval_request_id": "apr_f4a90d12b7c8"
    }
  ],
  "total": 2,
  "limit": 2,
  "offset": 0
}
```

`payload` is free-form and depends on `action_type` (do not build UI logic on it beyond showing key/value pairs).
`approval_request_id` links to an approval (`GET /v1/approvals/{id}`).

### `GET /v1/decisions/{decision_id}`

One decision, same shape as an item above. Unknown id: 404 `not_found`.

```bash
curl -s http://localhost:8080/v1/decisions/dec_6f1c0a7e2b11
```

```json
{
  "id": "dec_6f1c0a7e2b11",
  "ts": "2025-12-30T09:14:22Z",
  "action_type": "attach_invoice",
  "summary": "Found invoice AS-69664 in Grazyna's Downloads, attached it to the payment of EUR 623.48.",
  "level": "AUTO",
  "reason": "Routine: known supplier, amount within 20 % of the typical payment.",
  "status": "executed",
  "actor": "agent",
  "payload": {
    "transaction_id": "txn_1120",
    "invoice_number": "AS-69664",
    "amount": "-623.48",
    "source": "grazyna/Downloads/AS-69664.pdf",
    "employee_notified": "grazyna"
  },
  "executed_simulated": false,
  "approval_request_id": null
}
```

## `GET /v1/rules`

Policy rules the CEO set by writing a sentence in Slack (demo step 7), newest first. Paginated. Read from the `rules` table (empty until a rule is created); the example below is illustrative. A rule applies
when all its non-null match fields (`category`, `supplier`, `action_type`) match; the newest matching active rule wins.

| Query | Default | Meaning |
|---|---|---|
| `limit`, `offset` | `50`, `0` | pagination |
| `active` | all | `true` / `false` |

```bash
curl -s "http://localhost:8080/v1/rules?limit=1"
```

```json
{
  "stub": false,
  "items": [
    {
      "id": "rule_3c9d7a1e5f20",
      "text": "I always approve equipment myself",
      "level": "APPROVAL",
      "category": "equipment",
      "supplier": null,
      "action_type": null,
      "created_by": "vincenzo",
      "created_at": "2025-12-31T10:20:00Z",
      "active": true
    }
  ],
  "total": 3,
  "limit": 1,
  "offset": 0
}
```

---

## `GET /v1/approvals`

Approval requests: the CEO decisions the agent waits for (demo step 5), newest first. Each item embeds the decision it
is about (`action_type`, `summary`, `payload`, `level`), so one call is enough to draw an Approve / Reject card.

| Query | Default | Meaning |
|---|---|---|
| `state` | all | `pending` (waiting), `resolved` (approved or rejected), `approved`, `rejected` |
| `limit`, `offset` | 50, 0 | pagination |

```bash
curl -s "http://localhost:8080/v1/approvals?state=pending"
```

```json
{
  "stub": false,
  "items": [
    {
      "id": "apr_8e2f6b3a1c47",
      "decision_id": "dec_5d0a91c4e7b2",
      "approver_id": "vincenzo",
      "question": "Apply for a credit line of 7,000 EUR from 1 Mar to 5 Aug 2026? The forecast falls 5,203 EUR below the safety buffer on 2 Jul.",
      "status": "pending",
      "created_at": "2026-11-20T18:02:11Z",
      "resolved_at": null,
      "channel_ref": "D01ABCDEF:1763661731.000200",
      "decision": {
        "id": "dec_5d0a91c4e7b2",
        "ts": "2026-11-20T18:02:11Z",
        "action_type": "request_credit_line",
        "summary": "Apply for a credit line of 7,000 EUR",
        "level": "APPROVAL",
        "reason": "Credit lines always need CEO approval.",
        "status": "pending",
        "actor": "agent",
        "payload": {"amount": "7000.00"},
        "executed_simulated": false,
        "approval_request_id": "apr_8e2f6b3a1c47"
      }
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/approvals/{approval_id}`

One approval, same shape as an item above. Unknown id: 404 `not_found`.

### `POST /v1/approvals/{approval_id}:approve` and `:reject`

Demo step 6: the CEO clicks Approve. Calls `Agent.resume_approval`: on approve the agent executes the action (always
**SIMULATED**, D-005) and records it in the decision history; on reject nothing is executed and the decision becomes
`rejected`. The approver also gets a confirmation DM.

Body: `{"by": "<employee id>"}` (the dashboard has no login, so it sends who clicked; `vincenzo` for the CEO). When the server has `API_WRITE_TOKEN` set, send `Authorization: Bearer <token>`; `by` is then ignored and the answer is recorded as the CEO ([Write protection](#write-protection)).

```bash
curl -s -X POST http://localhost:8080/v1/approvals/apr_8e2f6b3a1c47:approve \
  -H 'Content-Type: application/json' -d '{"by": "vincenzo"}'
curl -s -X POST http://localhost:8080/v1/approvals/apr_8e2f6b3a1c47:reject \
  -H 'Content-Type: application/json' -d '{"by": "vincenzo"}'
```

```json
{
  "id": "apr_8e2f6b3a1c47",
  "decision_id": "dec_5d0a91c4e7b2",
  "approver_id": "vincenzo",
  "question": "Apply for a credit line of 7,000 EUR from 1 Mar to 5 Aug 2026? ...",
  "status": "approved",
  "created_at": "2026-11-20T18:02:11Z",
  "resolved_at": "2026-11-20T18:05:40Z",
  "channel_ref": "D01ABCDEF:1763661731.000200",
  "decision": {
    "id": "dec_5d0a91c4e7b2",
    "action_type": "request_credit_line",
    "status": "executed",
    "actor": "vincenzo",
    "executed_simulated": true,
    "summary": "Apply for a credit line of 7,000 EUR Approved by vincenzo. Execution is SIMULATED.",
    "...": "..."
  },
  "already_resolved": false
}
```

| Situation | Result |
|---|---|
| first answer | 200, `already_resolved: false`, `status` `approved` / `rejected` |
| same answer again (double click, Slack and dashboard both) | 200, `already_resolved: true`, nothing runs twice, the stored state is returned |
| the opposite answer after resolution | 409 `conflict`, state unchanged |
| `by` is not the approver | 403 `forbidden`, nothing happens |
| token required but missing or wrong | 401 `unauthorized`, nothing happens |
| unknown id | 404 `not_found` |
| missing or empty `by` | 422 `validation_error` |
| API process has no agent | 503 `agent_unavailable` |

The agent is injected through the FastAPI dependency `cfo.api.deps.get_agent`. The process that builds it
(`cfo.run`) calls `cfo.api.deps.set_agent(agent)` before serving; tests override the dependency with a fake.

---

## `GET /v1/events` (Server-Sent Events)

A live stream for the dashboard: new decisions, approvals and simulator events, without polling from the browser.

```js
const es = new EventSource("http://localhost:8080/v1/events");
es.addEventListener("decision", (e) => addToHistory(JSON.parse(e.data)));
es.addEventListener("approval", (e) => updateCard(JSON.parse(e.data)));
es.addEventListener("event", (e) => console.log("simulator event", JSON.parse(e.data)));
```

```bash
curl -N http://localhost:8080/v1/events
```

```text
id: decision=2026-11-20T18:02:11.120000+00:00|dec_5d0a91c4e7b2;approval=...;event=...
retry: 3000
: connected

id: decision=2026-11-20T18:02:11.120000+00:00|dec_5d0a91c4e7b2;approval=...;event=...
event: decision
data: {"id": "dec_5d0a91c4e7b2", "ts": "2026-11-20T18:02:11.120000Z", "action_type": "request_credit_line", ...}

id: decision=...;approval=2026-11-20T18:02:11.340000+00:00|apr_8e2f6b3a1c47;event=...
event: approval
data: {"id": "apr_8e2f6b3a1c47", "status": "pending", "decision": {...}, ...}

: keepalive
```

| `event:` | `data` | When |
|---|---|---|
| `decision` | like `GET /v1/decisions/{id}` | a decision row is written |
| `approval` | like `GET /v1/approvals/{id}` | an approval is created, and again when it is answered (status and embedded decision are then the resolved ones) |
| `event` | `{id, ts, kind, payload}` | the simulator wrote an event (bank transaction, invoice document, metric) |

* **Start:** without `Last-Event-ID` the stream starts from now: nothing is replayed. Load the current state with the
  list endpoints first, then open the stream.
* **Resume:** every message, the first frame (`: connected`) included, carries an `id:` (`-` when the tables were empty at connect time), an opaque token (one cursor per table: timestamps of events are
  simulated time, those of decisions are wall clock, so there is no single sequence number). `EventSource`
  sends it back as `Last-Event-ID` after a reconnect and receives everything newer, once. Clients that cannot set
  the header can use `?last_event_id=<token>`. An unusable token is ignored (start from now).
* A decision that changes in place (pending to executed) is not sent again as `decision`; follow the `approval`
  message, which carries the resolved decision.
* **Mechanics:** the stream is an async generator: an idle connection holds no worker thread, the server polls the database about once a second per connection (each poll is a short query) and sends
  `: keepalive` comments after 15 idle polls; `retry: 3000` tells the browser to reconnect after 3 s. Behind a proxy turn
  response buffering off (`X-Accel-Buffering: no` is already sent). CORS is open like for the other endpoints.
* Poll interval, wait function and connection factory are the injectable dependency
  `cfo.api.deps.get_stream_config`, which is how the tests run without sleeping.

---

## Simulator control

Replay of December (demo step 2 onwards), state in the database table `sim_state`, implemented by `cfo.sim.control`.

| Endpoint | Body | Effect |
|---|---|---|
| `GET /v1/sim` | none | status |
| `POST /v1/sim:start` | optional `{"speed": 120}` (sim seconds per real second, > 0) | un-pause, optionally change speed |
| `POST /v1/sim:pause` | none | pause (events due until now are emitted first) |
| `POST /v1/sim:step` | none | emit the next event right now |

`jump` and `reset` are not exposed (CLI only: `python -m cfo.sim jump 2025-12-30`).

```bash
curl -s http://localhost:8080/v1/sim
curl -s -X POST http://localhost:8080/v1/sim:start -H 'Content-Type: application/json' -d '{"speed": 3600}'
curl -s -X POST http://localhost:8080/v1/sim:pause
curl -s -X POST http://localhost:8080/v1/sim:step
```

```json
{
  "sim_time": "2025-12-01T00:00:00Z",
  "paused": true,
  "speed": 3600.0,
  "emitted": 0,
  "total": 106,
  "next_event_ts": "2025-12-01T07:40:00Z",
  "finished": false
}
```

`:step` returns `{"event": {"id", "ts", "kind", "payload"} | null, "status": {...}}`; `event` is `null` when the
replay is finished.

**Caveat: subscribers live in the worker process.** The agent listens to the simulator through
`cfo.sim.subscribe(callback)`, a registry of the *current process*. Therefore:

* `start` and `pause` only change the shared state in the database; the clock advances in the process that runs
  `python -m cfo.sim run` (the worker, `cfo.run`). Starting the replay from the API without a running worker moves
  nothing.
* `step` writes the event in the API process and hands it to the subscribers of the API process. If the agent is
  registered in the worker only, the event shows up in the database (and in `/v1/events`) but the agent does not react
  to it. Run the agent in the API process (single-process deployment) or use `start`/`pause` and let the worker tick.
* The default control opens its own database connection per call and reads the events from `data/generated`.


## `GET /v1/llm/usage` and `GET /v1/llm/usage/live`

What running the agent costs on the cluster (T032, D-019). Two data sources:

* **`llm_usage`**: one row per LLM call made through `cfo/llm.py` (time, model, prompt and completion tokens from the
  server's `usage` field, latency, and which agent step it belonged to: event id and type, purpose such as
  `extract_invoice` or `explain`, the decision the event ended with). Tokens are *estimated* (about 4 characters per
  token) when the server sends no `usage`; such calls are flagged and counted in `estimated_requests`. Recording never
  breaks a call: a database failure is logged and skipped. `FakeLLM` records too (model `fake`).
* **`vllm_metrics_samples`**: snapshots of the vLLM `/metrics` endpoint, taken by `python -m cfo.usage sample --every 60`
  (or `cfo.usage.sampler.start_background()` from the worker). Deltas between snapshots give the usage of the whole
  server over any range and survive pod restarts (a counter that goes down is treated as a restart). `VLLM_METRICS_URL`
  unset = no sampler; `/usage` still works from `llm_usage` alone and `/usage/live` answers 503.

### Cost model (all rates are assumptions, listed in the response)

| Method | Formula |
|---|---|
| `cost_busy_time` | sum of request latency x hourly rate. Overlapping requests count twice: an upper bound of the GPU time used |
| `cost_amortized` | wall-clock hours of the window x hourly rate x **our share** of the tokens the server processed (`llm_usage` tokens / vLLM `prompt + generation` token deltas). Share is 1.0 when there are no vLLM samples (`token_share_source: assumed_all_traffic`). The window starts at the first recorded data and ends at `min(to, now)`; buckets without requests are not listed, so `total` can exceed the sum of listed buckets (idle time) |
| `hosted_comparison` | the same prompt and completion tokens at a hosted model's list price per 1M tokens, converted to EUR (`usd_to_eur`) |

Hourly rate = `GPU_HOURLY_COST_EUR` + `NODE_HOURLY_COST_EUR`. Default GPU rate **0.70 EUR/h**: one NVIDIA L4 on demand,
AWS EC2 g6.xlarge (1x L4) USD 0.8048/h in us-east-1 (https://instances.vantage.sh/aws/ec2/g6.xlarge, read 2026-11-20, an
aggregator), at an assumed 0.87 EUR/USD, rounded. Default hosted list prices (read 2026-11-20): gpt-5-mini USD 0.25 in /
2.00 out per 1M tokens (https://developers.openai.com/api/docs/models/gpt-5-mini), gpt-5-nano USD 0.05 / 0.40
(https://developers.openai.com/api/docs/models/gpt-5-nano). Override with `HOSTED_PRICES_JSON`. These are not quotes.

**Costs use six decimals** (`{"amount": "0.001944", "currency": "EUR"}`): one call costs fractions of a cent. This is
the one exception to the two-decimal money rule.

### `GET /v1/llm/usage`

| Query | Default | Meaning |
|---|---|---|
| `from` | `to` minus 7 days | start (inclusive), ISO 8601, naive values are UTC |
| `to` | now | end (exclusive) |
| `group_by` | `day` | `day`, `hour` (UTC buckets), `event_type` or `purpose` |

Response: `total` and `items` (one per bucket: requests, failed and estimated requests, tokens, p50/p95 latency in ms,
busy seconds, both costs), `per_action` (cost per decision, by action type: LLM calls are attributed to the *last*
decision an event produced; `cost_per_action_*` divides by all decisions of that type in the range), `per_event`,
`cluster` (vLLM totals for the range incl. restarts seen, split by `finished_reason`, cached vs computed prompt tokens,
GPU time and its cost, p50/p95 latency from the histogram; null without samples), `hosted_comparison` and
`assumptions` (rates, sources, window hours, token share, method descriptions, notes). `from > to` is a 422.

```bash
curl -s 'http://localhost:8080/v1/llm/usage?from=2026-11-20T00:00:00Z&to=2026-11-22T00:00:00Z&group_by=day'
```

```json
{
  "stub": false,
  "currency": "EUR",
  "from": "2026-11-20T00:00:00Z",
  "to": "2026-11-22T00:00:00Z",
  "window_from": "2026-11-20T09:00:00Z",
  "window_to": "2026-11-21T10:01:00Z",
  "group_by": "day",
  "total": {
    "key": "total",
    "bucket_start": null,
    "requests": 4,
    "failed_requests": 0,
    "estimated_requests": 1,
    "prompt_tokens": 2900,
    "completion_tokens": 500,
    "latency_p50_ms": 2500.0,
    "latency_p95_ms": 3849.9999999999995,
    "busy_seconds": 10.0,
    "cost_busy_time": {
      "amount": "0.001944",
      "currency": "EUR"
    },
    "cost_amortized": {
      "amount": "8.385869",
      "currency": "EUR"
    }
  },
  "items": [
    {
      "key": "2026-11-20",
      "bucket_start": "2026-11-20T00:00:00Z",
      "requests": 3,
      "failed_requests": 0,
      "estimated_requests": 1,
      "prompt_tokens": 2300,
      "completion_tokens": 450,
      "latency_p50_ms": 3000.0,
      "latency_p95_ms": 3900.0,
      "busy_seconds": 9.0,
      "cost_busy_time": {
        "amount": "0.001750",
        "currency": "EUR"
      },
      "cost_amortized": {
        "amount": "5.028169",
        "currency": "EUR"
      }
    }
  ],
  "per_action": [
    {
      "action_type": "attach_invoice",
      "actions": 1,
      "requests": 2,
      "prompt_tokens": 1500,
      "completion_tokens": 300,
      "cost_busy_time": {
        "amount": "0.001167",
        "currency": "EUR"
      },
      "cost_amortized": {
        "amount": "4.439577",
        "currency": "EUR"
      },
      "cost_per_action_busy_time": {
        "amount": "0.001167",
        "currency": "EUR"
      },
      "cost_per_action_amortized": {
        "amount": "4.439577",
        "currency": "EUR"
      }
    }
  ],
  "per_event": {
    "events": 3,
    "cost_per_event_busy_time": {
      "amount": "0.000648",
      "currency": "EUR"
    },
    "cost_per_event_amortized": {
      "amount": "2.795290",
      "currency": "EUR"
    }
  },
  "cluster": {
    "samples": 3,
    "counter_resets": 1,
    "prompt_tokens": 6000,
    "generation_tokens": 1100,
    "requests": 14,
    "requests_by_finish_reason": {
      "stop": 14
    },
    "cached_prompt_tokens": 600,
    "computed_prompt_tokens": 5400,
    "busy_seconds": 29.7,
    "busy_seconds_source": "request_inference_time_seconds",
    "cost_busy_time_all_traffic": {
      "amount": "0.005775",
      "currency": "EUR"
    },
    "cost_busy_time_our_share": {
      "amount": "0.002765",
      "currency": "EUR"
    },
    "avg_e2e_latency_seconds": "2.357",
    "p50_e2e_latency_seconds": "1.889",
    "p95_e2e_latency_seconds": "4.689",
    "our_token_share": "0.4789"
  },
  "hosted_comparison": [
    {
      "model": "gpt-5-mini",
      "input_per_million": "0.25",
      "output_per_million": "2.00",
      "price_currency": "USD",
      "source": "https://developers.openai.com/api/docs/models/gpt-5-mini",
      "as_of": "2026-11-20",
      "cost": {
        "amount": "0.001501",
        "currency": "EUR"
      },
      "vs_busy_time": "0.77"
    }
  ],
  "assumptions": {
    "gpu_hourly_cost_eur": "0.70",
    "node_hourly_cost_eur": "0",
    "hourly_rate_eur": "0.70",
    "gpu_price_source": "AWS EC2 g6.xlarge (1x NVIDIA L4) on-demand USD 0.8048/h in us-east-1, https://instances.vantage.sh/aws/ec2/g6.xlarge, read 2026-11-20; converted at an assumed 0.87 EUR/USD and rounded to 0.70 EUR/h. Assumption, not a quote.",
    "window_hours": 25.0167,
    "token_share": "0.4789",
    "token_share_source": "vllm_metrics",
    "usd_to_eur": "0.87",
    "methods": {
      "busy_time": "Sum of request latency x hourly rate. Overlapping requests are counted twice, so this is an upper bound of the GPU time our requests used.",
      "amortized": "Wall-clock hours of the window x hourly rate x our share of the tokens the server processed (llm_usage tokens / vLLM token counters). Share is 1.0 when there are no vLLM samples. Buckets with no request are not listed, so the total can exceed the sum of the listed buckets (idle time).",
      "hosted": "The same prompt and completion tokens at a hosted API's list price per 1M tokens, converted to EUR at usd_to_eur. List prices change; they are assumptions with a date."
    },
    "notes": [
      "1 request(s) have estimated token counts (the server sent no usage field, about 4 characters per token)."
    ]
  }
}
```

(Shortened: `items`, `per_action` and `hosted_comparison` have more entries; example values come from a test fixture.)

### `GET /v1/llm/usage/live`

Newest vLLM snapshot and the token rates over the interval to the one before it. `kv_cache_usage` is a fraction 0..1
from `vllm:kv_cache_usage_perc` (older servers: `vllm:gpu_cache_usage_perc`, named in `kv_cache_metric`); it is null if
the server reports neither. `stale` is true when the sample is older than `VLLM_METRICS_STALE_SECONDS` (default 300): the
sampler is not running. **503 `metrics_unavailable`** when no sample is stored.

```bash
curl -s http://localhost:8080/v1/llm/usage/live
```

```json
{
  "stub": false,
  "model": "modelcar-granite-4-0-h-tiny",
  "sampled_at": "2026-11-21T10:00:00Z",
  "age_seconds": 60.0,
  "stale": false,
  "requests_running": 2,
  "requests_waiting": 1,
  "kv_cache_usage": "0.3700",
  "kv_cache_metric": "vllm:kv_cache_usage_perc",
  "prompt_tokens_per_second": "0.21",
  "generation_tokens_per_second": "0.06",
  "interval_seconds": 7200.0,
  "counter_reset": false
}
```

### Operating it

| Variable | Default | Meaning |
|---|---|---|
| `VLLM_METRICS_URL` | unset (sampler off) | in-cluster `http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/metrics` (plain HTTP since Granite runs as a KServe InferenceService, D-020); locally `oc port-forward svc/modelcar-granite-4-0-h-tiny-predictor 8080:80 -n my-first-model` (headless service, port 80 → pod 8080) then `http://localhost:8080/metrics` |
| `VLLM_METRICS_VERIFY` | `true` | TLS check for an `https://` URL: `true`, `false` (self-signed certificate) or a CA bundle path; ignored for `http://` |
| `VLLM_METRICS_BEARER_TOKEN` | unset | only if the endpoint needs a bearer token |
| `VLLM_METRICS_TIMEOUT` / `VLLM_METRICS_STALE_SECONDS` | `5` / `300` | scrape timeout, staleness limit of `/live` |
| `GPU_HOURLY_COST_EUR` / `NODE_HOURLY_COST_EUR` | `0.70` / `0` | rates, see above |
| `HOSTED_PRICES_JSON` | gpt-5-mini, gpt-5-nano | list of `{"model", "input_per_million", "output_per_million", "currency", "source", "as_of"}` |
| `USD_TO_EUR` | `0.87` | converts the (USD) hosted list prices |
| `LLM_USAGE_RECORD` | `true` | `false` switches the per-call recording off |

Scrape ONE server pod: counters of several replicas behind one service would look like restarts. The sampler
(`python -m cfo.usage sample --every 60`) is a separate process or a thread of the worker; it needs the same
`DATABASE_URL`.

---

## Coming next

Nothing planned that the dashboard depends on right now.

Missing something for the dashboard? Open a GitHub issue with the screen and the fields you need (D-010).
