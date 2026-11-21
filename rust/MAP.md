# Whole-application migration map

Goal: all Scout application behavior implemented in Rust, original retained as reference.
Branch: rewrite/rust-anything.
Decisions: [DECISIONS.md](DECISIONS.md). Domain names: [GLOSSARY.md](GLOSSARY.md).

This is a work-in-progress coverage map. No whole-app equivalence or lift is claimed.

| Wave | Unit | State | Proof required |
| --- | --- | --- | --- |
| 0 | Source analysis and dependency research | claimed by root 2026-11-21 | raw rust-anything plans plus audited inventories |
| 0 | Original runtime and isolated recordings | claimed by root 2026-11-21 | original replay 100%; effects/DB fixtures |
| 1 | Auth primitives and tenant validation | auth 801-case gate; platform 142-case comparison and isolated DB checks; HTTP integration active | recorded valid/invalid identities, passwords and schema isolation |
| 1 | Policy classification and transaction context | done (unit gate; integration pending) | Decimal thresholds, normalization, rule precedence and reasons |
| 1 | Dashboard help and public metadata | help 216-case gate; system health wired, HEAD/CORS comparison active | every topic and unknown/invalid input |
| 1 | Financial models and formatting | finance/invoice 399-case gate; remaining models open | money overflow, cents, rounding, periods, filtering |
| 2 | Database repositories, tenancy and migrations | open | existing-schema compatibility, atomicity, tenant isolation |
| 2 | Durable tasks, jobs, approvals, audit, DM claims | job queue 8-case replay + real concurrency/backoff tests; other worker state open | concurrency, lease fencing, retry/dedupe and visibility |
| 2 | Forecast model, features, schedules, bands | kernels 1,102-case gate; artifact/model 346-case gate; runtime training 389-case gate; liquidity review 352-case gate; MLflow transport/bridges open | recorded inference and artifacts, numerical parity |
| 3 | API and dashboard server authentication/routes | Scout repository 36 direct-handler comparisons; financial service 30-case comparison + 295 cost cases + DB cold-start; native HTTP integration active; dashboard server open | authenticated HTTP/error/stream/effect recordings |
| 3 | Bank/files/mail/chat/KSeF/MCP connectors | open | isolated external protocol and side-effect replay |
| 3 | Worker loops, strategy, LLM/guardrails/usage | native LLM client 141-case comparison; queue/task state verified; loops/guardrails/integration open | end-to-end approvals, masking, failover and effects |
| 4 | Dashboard Rust SSR and browser interactions | claimed by dashboard_research; Leptos auth/shell in progress | all views, onboarding, chat, charts, light/dark/mobile parity |
| 5 | Packaging, CI, deployment/cutover | open | no Node/Python runtime fallback; gate and lift; clean startup |

Open factual risks are recorded under research/. Generated PORT_MAP.md reports only tool-visible units and must not be mistaken for total feature coverage.
