# Codex review: status of every finding (handoff for the next Claude session)

Read this before touching the review fixes. It tracks where each finding from the Codex review of 2026-11-20 ended up.
- Full finding list with file:line: `.house/reports/review-fixes.md` (items numbered 1–27 + P3).
- Per-task review text: `.house/reports/review-<T###>.md`. Fix-task reports: `.house/reports/T040.md` … `T046.md`.
- Rules used: D-016 (Codex reviewed everything, daily budget raised to 40), D-018 (max 3 Codex rounds per fix task; in the last round fix only what breaks the demo or the deploy guide, reject the rest with a reason in the task report).

Legend: ✅ on main · 🟡 fixed on a task branch, Codex-reviewed, `house check` GREEN, waiting in the merge queue · ⚪ deliberately not fixed · 🔴 open.

## Merge state (2026-11-20 ~18:00)
- On main (`done`): T045, T040, T044, T042.
- Merge queue, in this order (session collabothon-2026-a3 runs `house merge` sequentially): T043 (merging) → T041 → T046.
- Coordination: session `collabothon-2026-ed` merges T028 only after a3 reports "queue empty". Never run two `house merge` at once.
- After T041 merges: session ed re-runs the T028 agent eval against the real Granite to confirm invoice extraction works through the no-tools fallback.

## P0 — the cluster demo cannot run
| # | Finding | Where | Status |
|---|---|---|---|
| 1 | `python -m cfo.run` missing, worker crash-loops | entrypoint, worker.yaml | ✅ T011 |
| 2 | REST approvals 503 (agent in another pod) | api/deps.py | ✅ T011 (`AGENT_IN_API=1`) |
| 3 | Sim routes 500 (no `cfo-data` mount in api) | api.yaml | ✅ T011 + manifest test in T042 |
| 4 | Slack adapter gets an employee-id string, uses `.id` | channels/slack.py | ✅ T011 adapter (`AgentNotifier` in run.py) + call-shape test in T043 |
| 5 | CEO "oven" question never reaches the liquidity review | forecast/liquidity.py | ✅ T011 (`cfo/chat.py`) |
| 6 | `CHANGE-ME` LLM placeholders | configmap.yaml | ✅ Granite 4.0 H Tiny (D-017) |
| 7 | vLLM returns 400 to every request with `tools` | llm.py | 🟡 T041 no-tools fallback. 🔴 **Human:** add `--enable-auto-tool-choice --tool-call-parser granite` to the Granite serving runtime (confirmed broken on the cluster by T028's eval, `.house/reports/T028.md`) |
| 8 | Image without the `mlflow` extra | Containerfile | ✅ T042 |

## P1 — wrong results in the demo story
| # | Finding | Status |
|---|---|---|
| 9 | Bank replay resets `documented`/`invoice_id` | ✅ T040 |
| 10 | `load --reset` keeps `sim_state` | ✅ T040 (also takes the sim lock) |
| 11 | Re-used invoice number overwrites another payment's invoice; races | ✅ T040 (FOR UPDATE + advisory lock, one lock order) |
| 12 | Attach by expected number instead of extracted | 🟡 T041 |
| 13 | `EXECUTED` after a refused attach | 🟡 T041 (ESCALATED + `payload.failed`) |
| 14 | CEO rule "alerts need approval" ignored | 🟡 T041 |
| 15 | Credit-line headline "before June" instead of "from 1 March" | 🟡 T041 (uses `period_start`) |
| 16 | Cost alert without invoice lines | 🟡 T041 |
| 17 | Odd small-model output aborts a turn (scalar `lines`, `[]`/`null` args, `1e309`, non-string tool name) | 🟡 T041 |
| 18 | Deploy guide skips the CSV upload; wrong `oc cp` syntax | ✅ T042 |

## P2 — hardening
| # | Finding | Status |
|---|---|---|
| 19 | Postgres / file server on 0.0.0.0 | ✅ T042 (127.0.0.1) |
| 20 | Workstation symlink escapes (company folder → private, employee dir → other employee), loops, NUL, unlogged `list_workstations` | ✅ T044 |
| 21 | Bot answers / errors / refusals posted publicly in the channel | 🟡 T043 (DM + ephemeral) |
| 22 | Anyone can approve as CEO; API unauthenticated | 🟡 T046 (`API_WRITE_TOKEN` in Secret `slack`; token ⇒ decisions recorded as CEO) |
| 23 | SSE holds threadpool slots; first frame without `id` | 🟡 T046 |
| 24 | Slack failure strands an approval | 🟡 T041 (`redeliver_approvals()` on the next event; not on a timer) |
| 25 | Seed not atomic; job deadline too short | ✅ T040 + T042 (3600 s) |
| 26 | Forecast backtest leaks the holdout | ✅ T045 (RMSE on organizer data unchanged: 307.5) |
| 27 | `step` advances several events; NaN/inf/0 speed | ✅ T044 |
| — | Cost rising from 0 gave `Infinity`, rejected by JSONB | ✅ T044 (`cost_change_pct` None) |

## P3
| Finding | Status |
|---|---|
| Supplier median by raw name; "above 1%" wording | 🟡 T041 |
| Escalation over last 500 decisions; reminder to someone on vacation | 🟡 T041 |
| Cost metric 0→x with zero metric; missing month | ✅ T044 |
| API: 500 without CORS, 405 without `Allow`, inverted range, `to=9999` | 🟡 T046 |
| `generate --out` deleted the whole dir | ✅ T040 (staging + manifest) |
| Empty `transactions`; `forecast_with` without fixed costs | ✅ T045 |
| HR CSV `slack_id`; refusal messages without level label | 🟡 T043 |
| December invoices dated after the sim clock; December orders +3% | ⚪ intentional / cosmetic |

## Deliberately not fixed (reasons in the task reports)
- T040: seed-vs-attach lock order, adversarial `scenario.json`, symlinks inside the output dir (our own dir; seed runs alone).
- T041: approval stays `APPROVED` after a failed execution (would change the API contract; failure is on the decision); one native tool success disables the fallback (endpoint capability does not change at runtime).
- T044: persisted NaN speed (every entry path validates now).
- T045: old artifacts keep the pre-fix RMSE (identical on real data; deploy retrains).
- T046: half-open date range that is valid after defaults returns 200 (422 only for an inverted effective range).

## Open — needs a human or a decision
1. 🔴 Granite serving args for tool calling (P0 7). Without them only the T041 fallback works.
2. 🔴 MLflow Route has no auth; API CORS is `*`. Keep until the dashboard URL is known, then restrict.
3. 🔴 PVC `cfo-data` is ReadWriteOnce and mounted by api, worker, mcp-workstations and the seed job. Fine on the 1-node cluster; needs RWX or events in Postgres otherwise.
4. 🔴 If `API_WRITE_TOKEN` is set, the dashboard must send `Authorization: Bearer …` on approve/reject/sim calls (tell Paweł / Piotrek).

## Next steps for whoever picks this up
1. Finish the merge queue (T043 → T041 → T046) with `house merge`, then tell session ed "queue empty".
2. Update the 🟡 rows above to ✅ once merged; keep this file the single status view.
3. After everything is on main: `house check collabothon-2026 --all` must be GREEN, then a milestone review `house review collabothon-2026 --since <commit before T040 merge>` for interactions between the fixes.
