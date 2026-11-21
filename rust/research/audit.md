# Native audit and permissions

`rust/audit` is the plain production crate; `audit-characterization` exercises the same source through a JSON/PyO3 boundary. It ports scout.audit general access checks, salary identification and SQL, Unicode-normalized whole-name attribution, ambiguous/reference-conflict rejection, cycle-safe report chains, salary visibility, conservative monthly payroll aggregation, and task-log access. Concrete Audit writes scoped PostgreSQL records, including source ALLOW/DENY/MASK decisions and 500-codepoint resource truncation.

479 saved original cases include 268 structured permission/payee/payroll/task cases. All 12 gate checks pass; the saved corpus replays in Rust tests. Unicode normalization is pinned to the actual Python 3.12 Unicode 15 tables: 2,130 non-ASCII→ASCII NFKD mappings and the existing captured casefold mapping. The implementation does not infer employee identity from partial names and does not expose a one-person aggregate. Source snapshots validate hashes on import. No Python is launched in production.

Database persistence/HR interaction tests passed in hr/tests/database.rs: two-tenant isolation, one-person DENY, two-person MASK, per-hidden-row records and 500-codepoint truncation. Worker integration and loop effects are not claimed by this pure gate.
