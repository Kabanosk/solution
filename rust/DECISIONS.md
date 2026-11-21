# Rust migration decisions

## ADR-001: Preserve the complete application contract
Status: accepted (2026-11-21, user requested the whole application)
Decision: port the Python API, background workers, connectors, financial and forecast logic, dashboard server and browser application. Preserve HTTP routes, database layouts, environment configuration, authentication/authorization, money semantics, and the existing Scout interface. No features are silently dropped.
Consequences: the original stays runnable as a reference until all units and whole-app replays pass. Partial Rust coverage is never reported as completion.

## ADR-002: Use rust-anything from the requested main branch
Status: accepted (2026-11-21)
Decision: use upstream commit ea00261bf69b069a300b9e0c8b0232832b287827, version 3.0.0, through its CLI. Record originals, port independently, diff, gate, wire, replay, then lift. Tool minimum Rust is 1.97; use installed stable 1.100 for this migration without changing the machine's default toolchain.
Consequences: all migration artifacts live in rust/. Tool-generated analysis remains separate from the manually audited coverage map.

## ADR-003: Isolate development and recordings
Status: accepted (2026-11-21, user requested another branch)
Decision: use branch rewrite/rust-anything; preserve the original source. Characterization runs use a local reference copy and test configuration with no production credentials. No production traffic is switched during implementation.
Consequences: a health/auth rejection recording proves only the public boundary, not authenticated feature parity. Database and external effects need their own isolated scenarios.

## ADR-004: Preserve Scout UI and target a Rust browser application
Status: implementation target (2026-11-21)
Decision: preserve semantic CSS tokens, layout, typography, Lucide paths, light/dark themes and responsive behavior. Target Leptos SSR/hydration on Axum; research UI dependencies before implementation. React/Radix/Recharts have no assumed drop-in equivalent. Retained JavaScript application logic means the full migration is incomplete.
Consequences: browser interaction and accessibility checks are required before replacing the existing dashboard.

## ADR-005: Native dependencies are distinct from source-language fallback
Status: accepted implementation convention (2026-11-21)
Decision: Rust application code may link existing native libraries, as ordinary Rust crates do, where required for model or XML compatibility. Spawning Python or Node remains explicit migration fallback and must be measured, never hidden.
Consequences: document libxgboost/XML signing dependencies and packaging. A fully Rust runtime is claimed only after original-language fallback is eliminated.

## ADR-006: Deployed runtime migration scope
Status: accepted (2026-11-21, explicit user clarification)
Decision: rewrite the deployed API, workers, integrations and dashboard in Rust. Retain Python notebooks and training/tuning tools as development utilities. This supersedes ADR-001 only for optional development tooling.
Consequences: model artifacts produced by Python training must load and run compatibly in Rust; deployed inference/feature/schedule/uncertainty calculations remain in scope. Node/Python subprocesses handling deployed application behavior remain migration fallback.
