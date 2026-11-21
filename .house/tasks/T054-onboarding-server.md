---
id: T054
title: Dashboard setup API and handoff
status: review
owner: native-subagent
branch: task/T054-onboarding-server
depends_on: []
acceptance:
  - id: AC-1
    run: "npm exec --yes --package=node@22 -- npm --prefix dashboard test"
    timeout: 600
  - id: AC-2
    run: "npm --prefix dashboard run typecheck"
    timeout: 120
  - id: AC-3
    manual: On the explicitly selected local test server, run onboarding callback and full-stack tests; verify no header bypass for other routes or secret transfer through handoff.
protected: []
---
See onboarding-contract.md for fixed contracts, ownership, security and verification.
