"""Autonomous CFO for Pizzeria Amalfi.

Module map (see .house/DECISIONS.md):
  cfo.config  settings from env / .env
  cfo.domain  typed domain models shared by every module
  cfo.llm     the only place that talks to an LLM (D-009)
  cfo.db      Postgres schema, migration, repository
"""
