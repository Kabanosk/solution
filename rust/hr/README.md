# Native company HR

`HR::new(Database,Tenant,EmployeeMap,EmployeeMap)` implements domain::People and `link_slack`. EmployeeMap preserves insertion order so repeated Slack IDs retain source last-wins behavior. Every read opens a verified tenant-scoped connection. The production crate compiles the exact shared HR algorithm from `../hr-characterization/src/port/native.rs`, with characterization-only memory storage disabled.

Preserves explicit Slack mapping before database fallback, Slack/name precedence, source role codes, inclusive/overlapping vacations, cycle-safe manager chains and all-on-vacation fallback. Legacy link_slack preserves registered Slack members and updates configured links atomically. The source's whitespace-only name error is retained.

411 source cases and a real two-tenant PostgreSQL test cover identity, names, roles, vacations, cycles, Slack links and combined audit/payroll privacy. See `../research/hr.md`, characterization gate and source provenance. Original Python remains reference-only.
