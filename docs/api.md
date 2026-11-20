# Autonomous CFO API

REST API behind the dashboard (Pizzeria Amalfi). Interactive docs: `http://localhost:8080/docs`. Machine-readable
contract: [`docs/openapi.json`](openapi.json), also served at `/openapi.json` (generate TypeScript types from it,
e.g. `npx openapi-typescript docs/openapi.json -o api.d.ts`).

**Status: all endpoints below return STUB data** (realistic Pizzeria Amalfi numbers, final response shapes).
Build the dashboard against them now; the shapes will not change when real data replaces the stubs, only the
values. Every stub response has `"stub": true` in the body and the header `X-Stub: true`.

## Run it

```bash
uv run --project backend uvicorn cfo.api.app:app --port 8080 --reload
```

No database is needed for the stubs. Allowed browser origins: env `CORS_ORIGINS` (comma-separated, default `*`).
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
| 409 | `conflict` | state does not allow the action (used by approvals, coming) |
| 422 | `validation_error` | bad query parameter; `error.details[]` lists `loc`, `message`, `type` |
| 500 | `internal_error` | bug on our side; no internal details are leaked |

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

```bash
curl -s http://localhost:8080/v1/kpis
```

```json
{
  "stub": true,
  "as_of": "2025-12-31",
  "balance": {
    "amount": "42338.93",
    "currency": "EUR"
  },
  "balance_change_30d": {
    "amount": "9393.42",
    "currency": "EUR"
  },
  "avg_monthly_inflows": {
    "amount": "44474.52",
    "currency": "EUR"
  },
  "avg_monthly_outflows": {
    "amount": "-42333.42",
    "currency": "EUR"
  },
  "forecast": {
    "horizon_days": 365,
    "lowest_balance": {
      "amount": "-20430.00",
      "currency": "EUR"
    },
    "lowest_date": "2026-06-27",
    "first_negative_date": "2026-06-06"
  },
  "undocumented_payments": {
    "count": 8,
    "total": {
      "amount": "6571.98",
      "currency": "EUR"
    }
  },
  "pending_approvals": 1,
  "open_cost_alerts": 1
}
```

## `GET /v1/forecast/liquidity`

Predicted end-of-day bank balance with an 80 % prediction interval, plus the stretches where the balance is
predicted negative (`gaps`, the reason for a credit line). `lowest_point` and `gaps` always cover the whole
horizon.

| Query | Default | Meaning |
|---|---|---|
| `horizon_days` | `90` | 7..365. Use 90 for the dashboard chart, 365 to see the June 2026 gap |
| `granularity` | `week` | `day` or `week`; spacing of `points` (weekly points are the last day of each 7-day block; the lowest day is always included) |

```bash
curl -s "http://localhost:8080/v1/forecast/liquidity?horizon_days=365"
```

Response (points shortened; the 90-day default has `"gaps": []`):

```json
{
  "stub": true,
  "as_of": "2025-12-31",
  "horizon_days": 365,
  "granularity": "week",
  "currency": "EUR",
  "opening_balance": {
    "amount": "42338.93",
    "currency": "EUR"
  },
  "lowest_point": {
    "date": "2026-06-27",
    "balance": {
      "amount": "-20430.00",
      "currency": "EUR"
    },
    "lower": {
      "amount": "-24746.00",
      "currency": "EUR"
    },
    "upper": {
      "amount": "-16114.00",
      "currency": "EUR"
    }
  },
  "gaps": [
    {
      "start": "2026-06-06",
      "end": "2026-07-26",
      "lowest_date": "2026-06-27",
      "lowest_balance": {
        "amount": "-20430.00",
        "currency": "EUR"
      }
    }
  ],
  "model": {
    "name": "xgboost-liquidity",
    "version": "stub",
    "mlflow_run_id": null,
    "trained_at": null
  },
  "points": [
    "..."
  ]
}
```

Each point: `{"date": "2026-01-07", "balance": {"amount": "41607.56", "currency": "EUR"}, "lower": {"amount": "41053.56", "currency": "EUR"}, "upper": {"amount": "42161.56", "currency": "EUR"}}`.

## `GET /v1/costs/by-team`

Costs per team over a range, with a monthly breakdown for stacked bars. Teams are sorted by total, highest first.

| Query | Default | Meaning |
|---|---|---|
| `from` | `2025-01-01` | first day (ISO date) |
| `to` | `2025-12-31` | last day (ISO date); `from` after `to` is a 422 |

```bash
curl -s "http://localhost:8080/v1/costs/by-team?from=2025-10-01&to=2025-12-31"
```

Response (list shortened; the full list has all 5 teams: kitchen, front, marketing, it, equipment):

```json
{
  "stub": true,
  "from": "2025-10-01",
  "to": "2025-12-31",
  "total": {
    "amount": "115029.82",
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
        "amount": "82321.71",
        "currency": "EUR"
      },
      "avg_monthly": {
        "amount": "27440.57",
        "currency": "EUR"
      },
      "share": "0.7157",
      "by_month": [
        {
          "month": "2025-10-01",
          "cost": {
            "amount": "27715.46",
            "currency": "EUR"
          }
        },
        {
          "month": "2025-11-01",
          "cost": {
            "amount": "26995.73",
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
      "share": "0.2243",
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
  "stub": true,
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

History of everything the agent decided or did, newest first (steps 2 to 7 of the demo). Paginated.

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
  "stub": true,
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
`approval_request_id` links to an approval (endpoints coming, see below).

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

Policy rules the CEO set by writing a sentence in Slack (demo step 7), newest first. Paginated. A rule applies
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
  "stub": true,
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

## Coming next (not in the skeleton yet)

Owned by other tasks, so the shapes below are plans, not contracts:

* `GET /v1/approvals`, `POST /v1/approvals/{id}:approve` and `:reject` (Approve / Reject from the dashboard),
  returning 409 `conflict` when already resolved.
* `GET /v1/events`: Server-Sent Events stream of new decisions and events.
* The stubs are replaced by database values endpoint by endpoint; `stub` will turn `false` and `X-Stub` disappears.

Missing something for the dashboard? Open a GitHub issue with the screen and the fields you need (D-010).
