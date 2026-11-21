---
id: T053
title: Backend setup and providers
status: review
owner: native-subagent
branch: task/T053-onboarding-backend
depends_on: []
acceptance:
  - id: AC-1
    run: "env DATABASE_URL=postgresql://pedro@127.0.0.1:55437/scout_backend SCOUT_TEST_DATABASE_URL=postgresql://pedro@127.0.0.1:55437/scout_backend TENANT_TEST_DATABASE_URL=postgresql://pedro@127.0.0.1:55437/scout_backend uv run --directory backend --frozen python -m pytest -p no:cacheprovider -q"
    timeout: 600
  - id: AC-2
    manual: Verify owner and tenant isolation, OAuth replay/cancellation, encrypted credential boundaries, and report missing live-provider prerequisites. Never target production.
protected: []
---
See onboarding-contract.md for fixed contracts, ownership, security and verification.
