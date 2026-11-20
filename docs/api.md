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
| Auth | none (demo) |
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
| 405 | `method_not_allowed` | wrong HTTP method |
| 403 | `forbidden` | `by` is not the approver of the approval |
| 409 | `conflict` | the approval was already answered the other way |
| 422 | `validation_error` | bad query parameter; `error.details[]` lists `loc`, `message`, `type` |
| 500 | `internal_error` | bug on our side; no internal details are leaked |
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
| `to` | last booking day | last day (ISO date); `from` after `to` is a 422 |

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

Body: `{"by": "<employee id>"}` (the dashboard has no login, so it sends who clicked; `vincenzo` for the CEO).

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
* **Resume:** every message carries an `id:`, an opaque token (one cursor per table: timestamps of events are
  simulated time, those of decisions are wall clock, so there is no single sequence number). `EventSource`
  sends it back as `Last-Event-ID` after a reconnect and receives everything newer, once. Clients that cannot set
  the header can use `?last_event_id=<token>`. An unusable token is ignored (start from now).
* A decision that changes in place (pending to executed) is not sent again as `decision`; follow the `approval`
  message, which carries the resolved decision.
* **Mechanics:** the server polls the database about once a second (inside the stream, per connection) and sends
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

---

## Coming next

Nothing planned that the dashboard depends on right now.

Missing something for the dashboard? Open a GitHub issue with the screen and the fields you need (D-010).
