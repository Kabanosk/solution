# Scout

Autonomous AI CFO that works with your whole team: it chases invoices, guards cash and your revenue plan, briefs you daily on what changed and asks before any big decision is made.

Built for **Collabothon 2026**. This README reproduces the project description from the [Scout project card](Scout_Collabothon_2026_Project_Card.pdf).

## The problem

Small business owners make financial decisions with information scattered across bank statements, invoices, email, chat and employee folders. Missing documents, unexplained spending and seasonal cash shortages create manual work and late surprises. Owners need someone to follow up with the team and explain what requires their attention.

## Our solution

Scout is an autonomous AI CFO that connects financial records with the context behind them. It investigates missing invoices, checks costs against business activity, forecasts liquidity and proposes next steps. Instead of stopping at an alert, it follows the issue through to a documented outcome. Routine work is automated; consequential decisions require human approval.

## The working prototype

In the Pizzeria Amalfi demo, users inspect transactions, invoices, agent tasks, evidence and an audit log. Scout searches authorized company sources for missing documents, reconciles amounts and requests help through Slack when needed. Owners can approve or reject actions, ask questions in chat and explore cash-flow forecasts and what-if spending scenarios. A separate Strategy loop produces cited news briefings using optional hosted research providers. Banking uses demo data or a sandbox; financial execution is simulated.

## Technology stack

| Area | Technologies |
| --- | --- |
| Frontend | TypeScript, Next.js, React, Tailwind CSS, Radix UI, Recharts, Lucide |
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic, LangGraph, MCP, PostgreSQL 16 |
| AI | IBM Granite 4.0 H Tiny, vLLM (OpenAI-compatible API), NeMo Guardrails |
| Forecasting / MLOps | XGBoost, scikit-learn, pandas, MLflow, Optuna, Jupyter |
| Integrations | Slack Bolt / Socket Mode, Google APIs / OAuth, bank API mock |
| Platform | Red Hat OpenShift AI, Kubernetes, Docker / Podman; NVIDIA L4 GPU |

## Behind the build

Python, FastAPI, LangGraph and PostgreSQL connect durable jobs, tools and approval checkpoints to a Next.js dashboard. MCP exposes company records, HR and authorized workstation files. IBM Granite runs through vLLM on Red Hat OpenShift AI; NeMo Guardrails screens prompts, documents and responses. XGBoost computes forecasts, with Jupyter, Optuna and MLflow supporting training, tuning, tracing and evaluation. We built the reconciliation workflows, policy engine, task and approval experience, connectors and dashboard around these existing tools. Financial calculations and policy checks stay in code; the LLM interprets context and explains results.

## Challenges and discoveries

Connecting a small self-hosted model to reliable tools required explicit tool-calling configuration and fallback parsing. Persistent jobs and approval state were essential when work paused for a person. We also found that a seasonal cash decline is not automatically a cash shortage: recommendations must use the actual balance and safety buffer. External documents need screening before entering model context, and connector setup must distinguish configured access from verified access.

## Results and next steps

We connected investigation, explained recommendations and owner responses in one auditable prototype. Recorded cluster smoke checks passed all 11 checks, including Granite tool calls, MCP reads and database access. Local verification covered policy, persistence, tenant isolation, approvals and responsive light/dark layouts. Validation uses organizer and synthetic demo data, with no measured customer savings yet. Next steps are broader live-provider validation, more representative forecast backtesting and production hardening before real financial execution.

## Source code

[Kabanosk/solution on GitHub](https://github.com/Kabanosk/solution)
