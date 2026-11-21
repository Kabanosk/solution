# Native access policy and audit

Production crate compiles the shared real implementation `../audit-characterization/src/port/native.rs`. `Audit::new(Database,Tenant)` implements domain::AuditSink and scoped database record/check/salary_filter/check_task methods. Resource truncation counts Unicode characters, matching Python's 500-character slice.

Pure helpers preserve general access policy, salary-row detection/SQL, exact full-name payee identity, manager reports, salary privacy, one-person aggregation denial and task-log access. Unicode 15 normalization/casefold data is captured from the original Python runtime. Unknown or ambiguous payees are treated as other people's pay. 479 recorded original cases match; all 12 rust-anything gate checks pass. Real two-tenant DB effect tests passed in hr/tests/database.rs.
