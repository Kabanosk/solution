# Scout: the AI CFO for small business

**🥉 3rd place at Collabothon 2026**, Commerzbank × Red Hat challenge *“Autonomous CFO”*. Built in one weekend, 20–22 November 2026.

A small business keeps its money data in five places: the bank, the invoicing system, the national e-invoice register (KSeF), Slack and employees' laptops. Nobody's job is to join them up. Scout is one agent that reads all five and does four things:

| | |
|---|---|
| **Finds** | the missing invoice, wherever it is hiding |
| **Flags** | cost spikes that orders do not explain |
| **Asks** | the owner before anything that matters |
| **Guards** | every message, tool result and reply |

It runs on an open-weights model (IBM Granite 4.0 H Tiny on vLLM) on Red Hat OpenShift AI, so company data stays on the cluster.

[![Scout in 66 seconds (click to play the video)](presentation/pitch/film-poster.jpg)](presentation/pitch/scout-promo.mp4)

## What it looks like

![Overview: cash, 90-day forecast with safety buffer, spend by team, what Scout did](docs/design/screens/live/v6-overview-1440-light.png)

**Finds.** A payment of €1,286.40 left the account and no invoice matched it. Scout checked the invoicing system, found the PDF in the office manager's company folders, checked the amount and filed it. Nobody had to do anything.

![Activity log of a resolved task](presentation/pitch/finds-log.jpg)

**Asks.** When nothing explains a payment, Scout asks the right person on Slack, in plain words, and lists every place it already looked. The answer is a reply in the same thread.

![Scout asking for a missing invoice on Slack](presentation/pitch/slack-dm.jpg)

**Flags.** The cloud bill of the ordering app rose by half in a month while orders grew 3%. Scout compared the two, named the line that grew (log ingestion) and told the owner.

**Asks, with numbers.** *“What if we buy a €30,000 oven in March?”* Scout forecasts cash, sees it fall below the safety buffer, explains the dip and proposes a credit line. It waits for the owner's approval, and the execution is simulated.

**Guards.** NeMo Guardrails check every user message, every tool result (a poisoned invoice is a tool result) and every reply. Salary access by role, the approval gate and the audit log are plain code and do not depend on the model.

## The owner stays in control

Every action shows how far Scout went:

| Level | Meaning |
|---|---|
| `AUTO` | Does it alone: matching, searching, filing |
| `NOTIFY` | Does it, then tells the owner why |
| `APPROVAL` | Waits for a yes. Decisions that matter, like a credit line, pause the run until the owner answers |
| `SIMULATED` | The bank request is prepared and logged; in this prototype no money moves |

The level is a plain function of amount, supplier and the owner's own rules (“I always approve equipment myself” becomes a rule). The model reads documents, connects tools and writes explanations; it never decides what it cannot be trusted with.

## Architecture

```mermaid
flowchart LR
  people["Owner and staff<br/>Slack · dashboard chat"]
  outside["Outside systems<br/>bank API (mock) · KSeF 2.0 (test env) · Slack"]
  subgraph ocp["Red Hat OpenShift AI · one node · one NVIDIA L4"]
    subgraph app["Project: Scout"]
      dash["Dashboard<br/>Next.js"]
      api["API<br/>FastAPI"]
      worker["Scout worker<br/>LangGraph loops, pause for approval"]
      mcp["MCP servers<br/>company DB · workstations · HR"]
      guard["NeMo Guardrails<br/>input · tool result · output"]
      fc["Forecast<br/>XGBoost + SHAP"]
      pg[("Postgres<br/>invoices · tasks · audit")]
      mlflow["MLflow<br/>traces · agent eval · forecast runs"]
    end
    subgraph model["Project: model"]
      llm["Granite 4.0 H Tiny<br/>vLLM · KServe"]
    end
  end
  people --> dash & worker
  outside --> worker
  dash --> api --> pg
  worker --> mcp & fc & pg
  worker <--> guard
  guard <--> llm
  worker -. traces .-> mlflow
```

Watchers poll the bank, the mailbox and the clock; workers run one loop per job (invoicing, cost control, liquidity, revenue plan, daily briefing, chat) and write a human-readable line for every step, so Slack and the dashboard show what the agent actually did.

## Results (as measured during the hackathon)

- **26/26** guardrail demo-kit checks with the expected verdict against live NeMo
- **14/15** agent evaluation scenarios with the real Granite model (MLflow)
- **1,000+** backend tests pass (a handful of known failures remain)
- **€23.7k → €9.2k** error of the 90-day balance forecast on 2025 data, before and after our forecast work
- An honest negative result: on a public two-year retail series the same forecast method lost to a calendar-only model ([notebook](notebooks/external_validation.py))

Simulated or partial: the bank is a mock API and no money moves, KSeF runs on the Ministry of Finance test environment, and the demo data is the organizers' sample company, *Pizzeria Amalfi*.

## Tech stack

| | |
|---|---|
| Frontend | TypeScript, Next.js, React, Tailwind CSS, Radix UI / shadcn/ui, Recharts, Lucide |
| Backend | Python 3.12, FastAPI, Pydantic, LangGraph, MCP, PostgreSQL 16 |
| AI | IBM Granite 4.0 H Tiny, vLLM (OpenAI-compatible API), NeMo Guardrails |
| Forecasting / MLOps | XGBoost, SHAP, scikit-learn, pandas, Optuna, MLflow, Jupyter |
| Integrations | Slack (Bolt, Socket Mode), Google OAuth and Gmail, KSeF 2.0 test environment, bank API mock |
| Platform | Red Hat OpenShift AI, KServe, Kubernetes, Docker / Podman, GitHub Actions |

## Repository

| Path | What is in it |
|---|---|
| [`backend/scout`](backend/scout) | The agent: loops, watchers, worker pool, connectors, Slack app, policy, guardrails, API |
| [`backend/cfo`](backend/cfo) | Forecasting (XGBoost), analysis, MCP servers, database, LLM client, usage and cost tracking |
| [`backend/tests`](backend/tests) | pytest suite |
| [`dashboard`](dashboard) | Next.js dashboard |
| [`deploy`](deploy) | OpenShift manifests: app, dashboard, database, forecast, guardrails, MLflow, CI |
| [`notebooks`](notebooks) | Forecast notebook (Jupyter on OpenShift AI), tuning and external validation |
| [`docs`](docs) | Deployment, auth, guardrails, KSeF, MLflow, design docs and the [project card](docs/project-card.pdf) |
| [`presentation`](presentation) | The pitch deck and the promo film |

## Running it

The live system ran on an OpenShift AI cluster provided for the hackathon, and that cluster was deleted after the event.

- **Pitch deck:** open [`presentation/pitch.html`](presentation/pitch.html) in a browser after cloning; it needs no server.
- **Backend tests:** start Postgres with `docker compose up -d db`, then
  ```bash
  cd backend
  SCOUT_TEST_DATABASE_URL=postgresql://cfo:cfo@127.0.0.1:5433/cfo uv run pytest
  ```
- **Dashboard:** see [`dashboard/README.md`](dashboard/README.md). It needs the API and a migrated Postgres.
- **Deploying to OpenShift:** [`docs/deploy.md`](docs/deploy.md) and [`docs/cluster-setup.md`](docs/cluster-setup.md). Cluster hostnames in `deploy/` are placeholders (`apps.<cluster-domain>`).

## How we built it

Four people, one weekend, with AI coding agents (Claude Code and Codex) doing much of the typing, coordinated by our own multi-agent setup. Task specs, decisions and review reports are in [`.house/`](.house), the agent definitions in [`.claude/`](.claude) and [`AGENTS.md`](AGENTS.md). The code exactly as it was at the end of the hackathon is at the tag [`collabothon-2026-submission`](https://github.com/Kabanosk/collabothon-2026/tree/collabothon-2026-submission).

## Team

Wojciech Fiołka · Piotr Warząchowski · Paweł Kojma · Grzegorz Kodrzycki
