---
id: T055
title: Optional workspace onboarding UI
status: review
owner: native-subagent
branch: task/T055-onboarding-ui
depends_on: []
acceptance:
  - id: AC-1
    run: "npm --prefix dashboard run typecheck"
    timeout: 120
  - id: AC-2
    run: "npm exec --yes --package=node@22 -- npm --prefix dashboard test"
    timeout: 600
  - id: AC-3
    manual: Inspect full-stack setup screenshots and keyboard, skip/resume, Google prerequisite, network form and agent dialog behavior at desktop/mobile in light/dark. Mocked provider responses are not live authorization proof.
protected: []
---
See onboarding-contract.md for fixed contracts, ownership, security and verification.
