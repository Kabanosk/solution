# Migration handoff (live work, not completion)

Read MAP.md and DECISIONS.md first, then the upstream whole-app skill at the requested rust-anything main branch. Upstream tool source commit is pinned in ADR-002; CLI 3.0.0 is installed locally. Current branch: rewrite/rust-anything. Original production sources have not been replaced or deployed.

User scope clarification: deployed API, workers, integrations and dashboard in Rust; Python notebooks/training/tuning remain development utilities (ADR-006). User explicitly authorized subagents.

Current independent agents: migration_research implements native forecast inference after policy/platform/Scout API; dashboard_research implements Leptos SSR/hydrated dashboard after auth/finance. Root owns jobs, original HTTP fixtures, Rust HTTP adapter and integration. Do not overwrite those claimed files.

Passed unit gates: help 216 cases; system 1 health case; policy 1615; auth 801; finance/invoice 399. See per-crate gate JSON and research reports. Platform has 142 direct source comparisons and isolated PostgreSQL checks. ScoutApi has 36 actual handler comparisons and isolated mutation tests. Queue has 8 application replays plus a real concurrency/fencing/backoff test. None of these proves full application parity.

HTTP corpus was expanded to 330 original cases with authenticated read fixtures and per-request DB/sequence dumps. Source launcher snapshots original Python packages, strips inherited credentials and uses only an explicitly selected local test DB. Root native app includes native auth and Scout read/enqueue routes, plus explicit proxy fallback for remaining behavior. Final HTTP replay passes 330/330 with database/sequence effects; 31/31 additional real-server comparisons and SSE delivery pass. One corpus case uses original fallback; this is not total application coverage. Task recorder additionally has 8/8 application replays and a real local DB regression test. Root is implementing the native LLM client/usage path under rust/llm; its compilation passes, characterization still pending.

Local-only test service: Podman container scout-rust-migration-db, loopback port 55433, dedicated database scout_migration. Scripts http-fixtures.py/http-db-dump.py refuse another DB. Fixtures are reset before HTTP replay and are separate from queue/platform test schemas. Never point recording at production. Raw commands/cases are tool-generated; executable Python must resolve from backend/.venv/bin through PATH because rust-anything canonicalizes explicit interpreter symlinks.

Remaining substantial work: forecast model loading/inference, worker task recorder/loops/watchers, Slack/Gmail/bank/KSeF/onboarding and credential services, dashboard native data/auth/strategy/usage routes, all browser views/interaction verification, model/artifact packaging, full zero-fallback integration coverage, lift and deployment changes. No cutover is authorized or implied by current partial coverage.
