# Daily CEO strategy review

Scout queues `strategy_review` daily at 07:00 Europe/Berlin (including weekends). The worker saves one `strategy` task per local calendar day. Open **Loops → Strategy** to read its outcome and source links, or choose **Run today's review** to enqueue it immediately. Repeated requests use the same daily job/task key. No Slack messages, purchases, hiring or financial commitments are made by this loop.

## Deployment

Apply the normal operator migration with the backend environment configured, before starting updated workers:

```sh
python -m cfo.db.migrate --migrate
python -m scout.worker
```

The migration upgrades the task loop constraint for registered existing tenant schemas and provisioning for new organizations. Deployment and production migration are not performed by the implementation task. The API, dashboard and worker all need the updated code.

Manual API: authenticated organization owner `POST /v1/strategy/review`. The dashboard proxies this through `POST /api/strategy/review`, with same-origin and session checks. Strategy notes and evidence are restricted to owners in dashboard reads and executives in backend reads.

## Evidence and limitations

Public sources: [ECB RSS](https://www.ecb.europa.eu/home/html/rss.en.html) and [Eurostat news RSS/Atom](https://ec.europa.eu/eurostat/web/rss). No API keys or company data are sent to these feeds. The reader bounds response size/time, rejects unsafe XML, ignores missing/future/older-than-14-day dates, deduplicates URLs and matches topics against ledger expense/revenue categories. Matching indicates potential relevance, not a verified company impact. Feed failure is reported separately from zero matches. These two macroeconomic feeds do not provide comprehensive local regulations, competitor intelligence or grants coverage.

Growth uses bank-collected revenue as a sales proxy, in one currency. It excludes the current month and first observed month, requires three consecutive completed positive-sales months ending last month, and computes median monthly growth over at most six months. The base scenario caps extrapolated monthly growth at ±20%; downside/upside use observed variability with a 2–10 percentage-point spread. Three monthly projections are scenarios, not a trained or backtested forecast. Missing history, stale bookings, seasonality and capacity constraints are disclosed. Other currencies are excluded rather than converted without an FX source. Existing liquidity forecasting is unchanged.

The mixed scorecard covers sales, operating cash surplus, owner income and expansion readiness. Sales can be assessed against an optional target. The other dimensions explicitly report missing reconciled operating flows, owner drawings, expansion costs, capacity assumptions and minimum reserves. Operating cash surplus is not accounting profit. The recommended next step for a proposed hire/location is the existing liquidity what-if.

Optional environment values for the legacy organization:

- `STRATEGY_CURRENCY` (default `EUR`)
- `STRATEGY_MONTHLY_REVENUE_TARGET`
- `STRATEGY_OPERATING_SURPLUS_TARGET`
- `STRATEGY_OWNER_INCOME_TARGET`

No target values are supplied by default. Nonlegacy organizations do not inherit these company-specific targets; tenant-editable goal storage remains future work. Operating-surplus and owner-income targets are recorded but cannot be assessed until their inputs are classified. The persisted `strategy_briefing` evidence contains the scorecard, monthly history, scenario assumptions, source availability and recommendations.

## Checks

Backend tests cover growth exclusions, sparse/stale data, RSS/Atom parsing, freshness, unavailable sources, daily scheduling, retry deduplication and tenant target isolation. Dashboard typecheck and unit tests cover the updated navigation. Light/dark desktop/mobile task rendering was checked with a temporary local fixture, removed afterward.
