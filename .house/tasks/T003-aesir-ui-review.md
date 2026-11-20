---
id: T003
title: Audit Aesir UI and accessibility
status: done
owner: aesir_ui_review
branch: feature/dashboard
depends_on: []
capabilities: [ui, accessibility, responsive-design, review]
acceptance:
  - id: AC-1
    manual: Compare the implementation against the public-snapshot Aesir tokens and component patterns.
  - id: AC-2
    manual: Audit keyboard focus, labels, chart semantics, reduced motion and contrast.
  - id: AC-3
    manual: Inspect responsive CSS at 375, 768, 1024 and 1440 px for overflow or hidden content risks.
protected:
  - dashboard/app.css
  - dashboard/src/main.rs
---

## Objective

Independently audit visual consistency with Antmicro Aesir Components, accessibility and responsive behavior of Overview and Staffing.

## Context

Review `/home/pawel/hackatons/collabothon2026/solution/dashboard/app.css`, `src/main.rs`, the vendored Aesir CSS and design-system master. Return a concise prioritized review with exact file paths and line numbers; do not modify files.

## Out of scope

Do not edit source, add dependencies, change product scope or commit.

