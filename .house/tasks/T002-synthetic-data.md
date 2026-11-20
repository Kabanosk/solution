---
id: T002
title: Synthetic company data consistent with the bank statement
status: done
owner: coder
branch: task/T002-synthetic-data
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_data.py"
    timeout: 900
  - id: AC-2
    run: "SOURCE_CSV=/home/wojciech.fiolka/house/contests/collabothon-2026/challenge/sample-data/pizzeria-amalfi-bank-statement-2025-v6.csv uv run --project backend python -m cfo.data.generate --out data/generated --seed 42 && test -d data/generated/workstations/grazyna"
    timeout: 900
protected: []
---
## Objective
A deterministic generator (`cfo.data.generate`) that loads `challenge/sample-data/pizzeria-amalfi-bank-statement-2025-v6.csv` (if missing, a copy committed under `data/source/`; see note) and produces everything else D-002 needs, and a loader that seeds Postgres.

## Context
- Bank CSV: `;`-separated, German decimals, dates `dd.mm.yy`, 1125 rows, 12 CHF. Supplier payments carry invoice numbers `AS-xxxxx` (Asimex) and `FLV-xxxxx` (Fattoria).
- Generate: supplier invoices (JSON + simple PDF) for AS/FLV payments, mostly in the DB; a handful missing from the DB but present on Grazyna's workstation (`workstations/grazyna/Downloads/*.pdf`, `workstations/grazyna/Mail/*.eml`), one with an amount mismatch, one missing everywhere. HR CSV (Vincenzo CEO; Grazyna office manager; Rosa head chef, manages food costs; Yusuf; Vanessa; vacations incl. Grazyna in December). Teams: kitchen/food, front, marketing, IT/app, equipment. Monthly ad spend + new customers, online orders, cloud invoices with FOCUS-like line items (one month +50% with flat orders), one equipment purchase.
- Split: history 01–11.2025 seeded; December 2025 written to `data/generated/events/` for the simulator.
- Note: `challenge/` is git-excluded (organizer material "for business use only"). Do not commit it; the generator reads the path from `SOURCE_CSV` env or the default. Ask the coordinator (Q-###) before committing the CSV anywhere.

## Out of scope
Agent logic, forecasting.
