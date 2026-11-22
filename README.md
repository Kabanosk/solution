# Project Name
Scout

## One-Line Summary
Autonomous AI CFO that works with your whole team: it chases invoices, guards cash and your revenue
plan, briefs you daily on what changed and asks before any big decision is made.

## Technology Stack
Frontend: TypeScript, Next.js, React, Tailwind CSS, Radix UI, Recharts, Lucide.
Backend: Python 3.12, FastAPI, Uvicorn, Pydantic, LangGraph, MCP, PostgreSQL 16.
AI: IBM Granite 4.0 H Tiny, vLLM (OpenAI-compatible API), NeMo Guardrails.
Forecasting / MLOps: XGBoost, scikit-learn, pandas, MLflow, Optuna, Jupyter.
Integrations: Slack Bolt / Socket Mode, Google APIs / OAuth, bank API mock.
Platform: Red Hat OpenShift AI, Kubernetes, Docker / Podman

## Project Overview
### THE PROBLEM
Small business owners make financial decisions with information scattered across bank statements, invoices, email, chat and employee folders. Missing documents and unexplained payments require management to chase people down to fill in the data into the system manually or perform investigation to find out what happened.

### YOUR SOLUTION
Scout is an autonomous AI CFO that connects financial records with the context behind them. It investigates missing invoices, checks costs against business activity, forecasts liquidity and creates daily summaries with the most important events related to the company and suggestions on how to increase revenue and help accelerate growth. The core idea is that Scout removes the overhead of being up to date with the news and manual labor that arises from people not being consistent with paperwork.

### THE WORKING PROTOTYPE
Scout investigates missing invoices, checks costs against business activity, forecasts liquidity and creates daily summaries with the most important events related to the company and suggestions on how to increase revenue and help accelerate growth. Scout is heavily integrated with existing systems present in most companies, like Slack, Gmail and KSeF (polish national invoice system).
Scout works in agent loop, identifies anomalies based on information in those systems. When such anomaly is identified, Scout goes through accessible data (from public Slack channels, mail history, invoice services) and fills the gap if possible. When there is not enough data to resolve all the issues it messages relevant people directly via Slack direct message and marks these actions in the system. Routine work is automated, final decisions require human approval.

### BEHIND THE BUILD
Core of Scout is an agent loop that works on LangGraph. Watchers in the database polls the bank, the mailbox, and the clock. Workers run the loop that own each job (invoicing, costs, liquidity, revenue plan, daily briefing, chat) and log every step, so Slack and dashboard show what agent actually did.

The model never decides what it cannot be trusted with. Autonomy (act, act and inform, ask first) is a plain function over amount, supplier and owner’s rules. The model only reads documents, connects existing components and writes explanations. Approvals pause a LangGraph run until the owner answers, so no action moves real money. NeMo Guardrails checks what comes in and what comes out, and each company has its own database schema.

The cash forecast is done by an XGBoost model. It predicts daily net cash flow from calendar features. Known scheduled payments are added on top. Every run is tracked in MLflow.

Everything runs on Red Hat OpenShift - the API, the workers, the dashboard, Postgres, MLflow and the guardrails service. Model (Granite 4.0 H Tiny served by vLLM on OpenShift AI) lies in seperate namespace. Company data stays in the cluster. Owners can also plug in an external model (e.g. OpenAI) with their own API key and it gets a short list of tools and no company data. Every push to main on GitHub triggers a build inside the cluster and rolls out only services that changed.

### CHALLENGES AND DISCOVERIES
The biggest challenge for us was to connect everything together: the agent loop with on premise Granite model, the slack integration, the database connection through MCP tools, the information presentation on the website, the external LLM providers with their own set of tools. Connecting a small self-hosted model to reliable tools required explicit tool-calling configuration and fallback parsing. We learned about the OpenShift tooling and infrastructure.

### RESULTS AND NEXT STEPS
We've set up the infrastructure on OpenShift with multiple pods being responsible for database, forecast, LLM hosting and dashboard website hosting. We tested in on debug environment using OpenShift. We are most proud of the agent loop that is actually working with the infrastructure and running actions using the MCP tools. Quality of forecasting, system performance and scalability needs to be improved still.
